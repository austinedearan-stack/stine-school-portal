"""TOTP multi-factor authentication (ARCHITECTURE.md §4.2).

* Secrets are encrypted at rest (MultiFernet) - audit A-9.
* A code is accepted only for a time-step later than the last accepted one, via a conditional
  UPDATE, so a code (or a concurrent duplicate submission) can never be replayed - audit A-7.
* Recovery and enrollment codes are stored as HMAC digests and consumed with a single conditional
  UPDATE bound to the user - concurrent reuse is impossible.
* First enrollment from a pre-auth (password-only) session requires a valid one-time enrollment
  code issued out of band, so a stolen password alone can never enroll an attacker's device - A-1.
"""

from __future__ import annotations

from datetime import timedelta

import pyotp
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from apps.accounts.models import MFADevice, MFAEnrollmentCode, MFARecoveryCode, TrustedDevice, User
from apps.core.authz import has_any_capability
from apps.core.capabilities import Role
from apps.core.crypto import decrypt, encrypt, keyed_digest, keyed_digests, normalise_code, random_code

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 16  # 80 bits
ENROLLMENT_CODE_LENGTH = 16
ENROLLMENT_CODE_TTL = timedelta(hours=72)
TOTP_VALID_WINDOW = 1  # +/- one 30-second step


def format_code(code: str) -> str:
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


# --- Devices --------------------------------------------------------------------------------------


def confirmed_device(user: User) -> MFADevice | None:
    return MFADevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def is_mfa_required(user: User) -> bool:
    """Admins, superadmins and every account holding a capability must use MFA (§4.1 step 5)."""
    return user.role in Role.ADMINS or has_any_capability(user)


def is_mfa_enforced(user: User) -> bool:
    """MFA must be completed at login: required by role/capability, or voluntarily enrolled."""
    return is_mfa_required(user) or confirmed_device(user) is not None


def provisioning_uri(user: User, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=settings.MFA_ISSUER_NAME)


def new_secret() -> str:
    return pyotp.random_base32()


def device_secret(device: MFADevice) -> str:
    return decrypt(device.secret_encrypted)


def _matching_step(secret: str, code: str, after_step: int) -> int | None:
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    now_step = totp.timecode(timezone.now())
    for step in range(now_step - TOTP_VALID_WINDOW, now_step + TOTP_VALID_WINDOW + 1):
        if step > after_step and constant_time_compare(totp.generate_otp(step), code):
            return step
    return None


def verify_totp(device: MFADevice, code: str) -> bool:
    try:
        secret = device_secret(device)
    except ValueError:
        return False
    step = _matching_step(secret, code, device.last_used_step)
    if step is None:
        return False
    won = MFADevice.objects.filter(pk=device.pk, last_used_step__lt=step).update(last_used_step=step)
    return won == 1


@transaction.atomic
def enroll(user: User, secret: str, code: str) -> list[str] | None:
    """Confirm ``secret`` with a first valid code and make it the user's only device.

    An existing device is replaced only here, after the new one is proven, so abandoning a
    replacement half-way never leaves the account without a working authenticator. Returns fresh
    recovery codes, or None if the code is wrong.
    """
    step = _matching_step(secret, code, after_step=-1)
    if step is None:
        return None
    User.objects.select_for_update().filter(pk=user.pk).first()  # serialise concurrent enrollments
    MFADevice.objects.filter(user=user).delete()
    TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
    MFADevice.objects.create(user=user, secret_encrypted=encrypt(secret), confirmed_at=timezone.now(),
                             last_used_step=step)
    return issue_recovery_codes(user)


@transaction.atomic
def remove_devices(user: User) -> None:
    """Delete every device and recovery code and revoke trusted devices (MFA reset / replacement)."""
    MFADevice.objects.filter(user=user).delete()
    MFARecoveryCode.objects.filter(user=user).delete()
    TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())


# --- Recovery codes -------------------------------------------------------------------------------


def issue_recovery_codes(user: User) -> list[str]:
    MFARecoveryCode.objects.filter(user=user).delete()
    codes = [random_code(RECOVERY_CODE_LENGTH) for _ in range(RECOVERY_CODE_COUNT)]
    MFARecoveryCode.objects.bulk_create(
        MFARecoveryCode(user=user, code_hash=keyed_digest(code, purpose="mfa-recovery")) for code in codes
    )
    return [format_code(c) for c in codes]


def remaining_recovery_codes(user: User) -> int:
    return MFARecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def use_recovery_code(user: User, candidate: str) -> bool:
    code = normalise_code(candidate)
    if len(code) != RECOVERY_CODE_LENGTH:
        return False
    used = MFARecoveryCode.objects.filter(
        user=user, code_hash__in=keyed_digests(code, purpose="mfa-recovery"), used_at__isnull=True
    ).update(used_at=timezone.now())
    return used == 1


def verify_second_factor(user: User, code: str) -> str | None:
    """Check a TOTP code, then a recovery code. Returns "totp", "recovery" or None."""
    device = confirmed_device(user)
    if device is None:
        return None
    if verify_totp(device, code):
        return "totp"
    if use_recovery_code(user, code):
        return "recovery"
    return None


# --- Enrollment codes -----------------------------------------------------------------------------


def issue_enrollment_code(user: User, issued_by: User | None) -> str:
    """Invalidate previous unused codes and issue a new one (to be delivered out of band)."""
    now = timezone.now()
    MFAEnrollmentCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    code = random_code(ENROLLMENT_CODE_LENGTH)
    MFAEnrollmentCode.objects.create(
        user=user, code_hash=keyed_digest(code, purpose="mfa-enrollment"), issued_by=issued_by,
        expires_at=now + ENROLLMENT_CODE_TTL,
    )
    return format_code(code)


def matching_enrollment_digest(user: User, candidate: str) -> str | None:
    """The stored digest of the live code ``candidate`` matches (kept in the session instead of the code)."""
    code = normalise_code(candidate)
    if len(code) != ENROLLMENT_CODE_LENGTH:
        return None
    return MFAEnrollmentCode.objects.filter(
        user=user, code_hash__in=keyed_digests(code, purpose="mfa-enrollment"), used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).values_list("code_hash", flat=True).first()


def check_enrollment_code(user: User, candidate: str) -> bool:
    return matching_enrollment_digest(user, candidate) is not None


def consume_enrollment_code(user: User, digest: str) -> bool:
    """Single conditional UPDATE by digest (the plaintext code is never kept in the session)."""
    used = MFAEnrollmentCode.objects.filter(
        user=user, code_hash=digest, used_at__isnull=True, expires_at__gt=timezone.now(),
    ).update(used_at=timezone.now())
    return used == 1

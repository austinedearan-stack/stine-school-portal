"""Authentication services (interim, Phase 2): adapted to the Phase 2 schema.

Phase 3 replaces the login flow with the throttled, enrollment-code based design of
ARCHITECTURE.md §4; the MFA primitives below (encrypted secret, step-counter replay guard,
HMAC-stored single-use recovery codes) are already the final ones.
"""

from __future__ import annotations

import pyotp
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from apps.accounts.models import MFADevice, MFARecoveryCode, User
from apps.core.audit import record_audit_event, record_security_event
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.core.crypto import decrypt, encrypt, keyed_digest, normalise_code, random_code
from apps.core.models import SecurityEventType

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 16  # 80 bits
TOTP_VALID_WINDOW = 1  # +/- one 30 s step

# Computed once so that unknown identifiers cost about as much as a real password check.
_DUMMY_HASH = make_password("dummy-password-for-timing-equalisation")  # noqa: S106 - not a credential


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_provisioning_uri(user: User, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=settings.MFA_ISSUER_NAME)


def confirmed_device(user: User) -> MFADevice | None:
    return MFADevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def is_mfa_required(user: User) -> bool:
    return user.role in Role.ADMINS or confirmed_device(user) is not None


def _matching_step(secret: str, code: str, after_step: int) -> int | None:
    """Return the TOTP time-step that ``code`` matches, if it is later than ``after_step``."""
    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    now_step = totp.timecode(timezone.now())
    for step in range(now_step - TOTP_VALID_WINDOW, now_step + TOTP_VALID_WINDOW + 1):
        if step > after_step and constant_time_compare(totp.generate_otp(step), code):
            return step
    return None


def verify_totp(device: MFADevice, code: str) -> bool:
    """Accept a code only for a time-step after the last accepted one (blocks replay, audit A-7)."""
    try:
        secret = decrypt(device.secret_encrypted)
    except ValueError:
        return False
    step = _matching_step(secret, code, device.last_used_step)
    if step is None:
        return False
    # Conditional update: of two concurrent submissions of the same code only one can win.
    won = MFADevice.objects.filter(pk=device.pk, last_used_step__lt=step).update(last_used_step=step)
    return won == 1


def check_new_secret(secret: str, code: str) -> bool:
    return _matching_step(secret, code, after_step=-1) is not None


@transaction.atomic
def enroll_device(user: User, secret: str) -> list[str]:
    """Store a confirmed device (secret encrypted at rest) and issue fresh recovery codes."""
    MFADevice.objects.filter(user=user).delete()
    MFADevice.objects.create(
        user=user,
        secret_encrypted=encrypt(secret),
        confirmed_at=timezone.now(),
        last_used_step=pyotp.TOTP(secret).timecode(timezone.now()),  # the enrollment code is spent
    )
    return issue_recovery_codes(user)


def issue_recovery_codes(user: User) -> list[str]:
    MFARecoveryCode.objects.filter(user=user).delete()
    codes = [random_code(RECOVERY_CODE_LENGTH) for _ in range(RECOVERY_CODE_COUNT)]
    MFARecoveryCode.objects.bulk_create(
        MFARecoveryCode(user=user, code_hash=keyed_digest(code, purpose="mfa-recovery")) for code in codes
    )
    return [f"{c[:4]}-{c[4:8]}-{c[8:12]}-{c[12:]}" for c in codes]


def use_recovery_code(user: User, candidate: str) -> bool:
    """Single conditional UPDATE: atomic single use, bound to this user."""
    code = normalise_code(candidate)
    if len(code) != RECOVERY_CODE_LENGTH:
        return False
    used = MFARecoveryCode.objects.filter(
        user=user, code_hash=keyed_digest(code, purpose="mfa-recovery"), used_at__isnull=True
    ).update(used_at=timezone.now())
    return used == 1


def resolve_identifier(identifier: str) -> User | None:
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    return User.objects.filter(username__iexact=identifier).first() or User.objects.filter(
        email__iexact=identifier
    ).first()


def authenticate_and_login(request, identifier: str, password: str):
    """Returns (user, status) with status in SUCCESS, MFA_REQUIRED, MFA_SETUP_REQUIRED, INVALID_CREDENTIALS."""
    ctx = RequestContext.from_request(request)
    user = resolve_identifier(identifier)
    if user is None:
        User(password=_DUMMY_HASH).check_password(password)  # equalise timing
        record_security_event(SecurityEventType.LOGIN_FAILURE, ctx=ctx, identifier=identifier)
        return None, "INVALID_CREDENTIALS"
    if not user.check_password(password) or not user.is_active:
        record_security_event(SecurityEventType.LOGIN_FAILURE, ctx=ctx, user=user, identifier=identifier)
        return None, "INVALID_CREDENTIALS"

    if is_mfa_required(user):
        request.session.cycle_key()
        request.session["pre_mfa_user_id"] = str(user.pk)
        if confirmed_device(user):
            return user, "MFA_REQUIRED"
        return user, "MFA_SETUP_REQUIRED"
    return complete_user_login(request, user)


def complete_user_login(request, user: User):
    ctx = RequestContext.from_request(request)
    request.session.pop("pre_mfa_user_id", None)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")  # rotates the session key
    record_security_event(SecurityEventType.LOGIN_SUCCESS, ctx=ctx, user=user)
    record_audit_event(user, "AUTH.LOGIN", user, ctx=ctx)
    return user, "SUCCESS"

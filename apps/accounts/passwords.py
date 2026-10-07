"""Password reset with an emailed one-time code typed into a POST form (ARCHITECTURE.md D15).

* No token ever appears in a URL.
* 10-character code from a 32-symbol alphabet (50 bits), stored as HMAC, valid 20 minutes, single use,
  at most 5 attempts; every unused code is invalidated by any password change.
* Identical response (and no extra request-thread work) whether or not the account exists.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.devices import revoke_all
from apps.accounts.models import PasswordResetCode, User
from apps.core.audit import record_audit_event, record_security_event
from apps.core.context import RequestContext
from apps.core.crypto import keyed_digest, normalise_code, random_code
from apps.core.mail import send_security_mail
from apps.core.models import SecurityEventType

CODE_LENGTH = 10
CODE_TTL = timedelta(minutes=20)
MAX_ATTEMPTS = 5


def generate_initial_password(length: int = 20) -> str:
    """Random password for operator-created accounts (shown once, changed by the user at first use)."""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits + "-_.!@#%"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in candidate) and any(c.isupper() for c in candidate) and any(
            c.isdigit() for c in candidate
        ):
            return candidate


def _digest(code: str) -> str:
    return keyed_digest(normalise_code(code), purpose="password-reset")


def request_reset(user: User | None, ctx: RequestContext) -> None:
    """Issue and email a code if the account exists and is active. Callers respond identically either way."""
    if user is None or not user.is_active:
        record_security_event(SecurityEventType.PASSWORD_RESET_REQUESTED, ctx=ctx, details={"result": "no_account"})
        return
    code = random_code(CODE_LENGTH)
    PasswordResetCode.objects.create(
        user=user, code_hash=_digest(code), expires_at=timezone.now() + CODE_TTL, created_ip=ctx.ip
    )
    record_security_event(SecurityEventType.PASSWORD_RESET_REQUESTED, ctx=ctx, user=user)
    send_security_mail(
        "Your password reset code",
        f"Your password reset code is: {code[:5]}-{code[5:]}\n\n"
        f"It expires in {int(CODE_TTL.total_seconds() // 60)} minutes and can be used once.\n"
        "If you did not ask to reset your password, ignore this email and consider changing your password.",
        user.email,
    )


def code_is_valid(user: User | None, code: str) -> bool:
    """Check the code; a wrong code spends one attempt on every live code of that user."""
    if user is None or not user.is_active:
        return False
    live = PasswordResetCode.objects.filter(
        user=user, used_at__isnull=True, expires_at__gt=timezone.now(), attempts__lt=MAX_ATTEMPTS
    )
    if live.filter(code_hash=_digest(code)).exists():
        return True
    live.update(attempts=F("attempts") + 1)
    return False


@transaction.atomic
def complete_reset(user: User, code: str, new_password: str, ctx: RequestContext) -> bool:
    used = PasswordResetCode.objects.filter(
        user=user, code_hash=_digest(code), used_at__isnull=True, expires_at__gt=timezone.now(),
        attempts__lt=MAX_ATTEMPTS,
    ).update(used_at=timezone.now())
    if used != 1:
        return False
    validate_password(new_password, user=user)
    set_new_password(user, new_password, ctx, reason="reset")
    record_security_event(SecurityEventType.PASSWORD_RESET_COMPLETED, ctx=ctx, user=user)
    return True


def set_new_password(user: User, new_password: str, ctx: RequestContext, *, reason: str) -> None:
    """Every password change: new hash (invalidates other sessions), kills reset codes and trusted
    devices, clears must_change_password, audits and notifies the user."""
    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "password_changed_at", "must_change_password", "updated_at"])
    PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    revoke_all(user)
    record_security_event(SecurityEventType.PASSWORD_CHANGED, ctx=ctx, user=user, details={"reason": reason})
    record_audit_event(user, "AUTH.PASSWORD_CHANGED", user, ctx=ctx, changes={"reason": reason})
    send_security_mail(
        "Your password was changed",
        "The password for your student portal account was just changed.\n"
        "If this was not you, contact IT support immediately.",
        user.email,
    )

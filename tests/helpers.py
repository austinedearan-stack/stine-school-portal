"""Test helpers shared by every suite."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pyotp

from apps.accounts import mfa, sessions
from apps.accounts.models import MFADevice


def login(client, user, *, mfa_verified: bool | None = None):
    """Log ``client`` in as ``user`` with a session that passes SessionPolicyMiddleware."""
    client.force_login(user)
    session = client.session
    now = int(time.time())
    session[sessions.AUTH_TIME_KEY] = now
    session[sessions.LAST_ACTIVITY_KEY] = now
    if mfa_verified if mfa_verified is not None else mfa.is_mfa_enforced(user):
        session[sessions.MFA_VERIFIED_KEY] = now
    session.save()
    return client


def enrol(user) -> str:
    """Give ``user`` a confirmed authenticator; returns the TOTP secret."""
    secret = pyotp.random_base32()
    assert mfa.enroll(user, secret, totp_code(secret)) is not None
    MFADevice.objects.filter(user=user).update(last_used_step=0)  # let tests use the current code again
    return secret


def totp_code(secret: str, offset_steps: int = 0) -> str:
    totp = pyotp.TOTP(secret)
    return totp.generate_otp(totp.timecode(datetime.now(UTC)) + offset_steps)

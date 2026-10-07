"""Device cookies (OWASP "device cookie" pattern, ARCHITECTURE.md §4.1).

After a successful full login the browser receives a signed, HttpOnly cookie binding (user, random
nonce). Login attempts that present a valid device cookie for the account being tried are throttled
in their own bucket, so an attacker hammering an account cannot lock its owner out of known devices.
The nonce is stored only as an HMAC digest; every device is revoked on password change or MFA reset.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from apps.accounts.models import TrustedDevice, User
from apps.core.crypto import keyed_digest

SALT = "portal.device-cookie"
MAX_AGE = timedelta(days=180)


def cookie_name() -> str:
    return getattr(settings, "DEVICE_COOKIE_NAME", "portal_device")


def issue(response, user: User) -> None:
    nonce = secrets.token_urlsafe(24)
    TrustedDevice.objects.create(user=user, nonce_hash=keyed_digest(nonce, purpose="device"))
    value = signing.dumps({"u": str(user.pk), "n": nonce}, salt=SALT, compress=True)
    response.set_cookie(
        cookie_name(), value, max_age=int(MAX_AGE.total_seconds()), httponly=True,
        secure=settings.SESSION_COOKIE_SECURE, samesite="Lax", path="/",
    )


def _parse(request) -> dict | None:
    raw = request.COOKIES.get(cookie_name())
    if not raw:
        return None
    try:
        data = signing.loads(raw, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return None
    return data if isinstance(data, dict) and {"u", "n"} <= set(data) else None


def valid_device_for(request, user: User | None) -> TrustedDevice | None:
    """The trusted device behind the request's cookie, if it belongs to ``user`` and is not revoked."""
    if user is None:
        return None
    data = _parse(request)
    if data is None or data["u"] != str(user.pk):
        return None
    device = TrustedDevice.objects.filter(
        user=user, nonce_hash=keyed_digest(str(data["n"]), purpose="device"), revoked_at__isnull=True,
        created_at__gte=timezone.now() - MAX_AGE,
    ).first()
    if device is not None:
        TrustedDevice.objects.filter(pk=device.pk).update(last_used_at=timezone.now())
    return device


def revoke_all(user: User) -> None:
    TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())

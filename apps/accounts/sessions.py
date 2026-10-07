"""Session state for the two-step login (ARCHITECTURE.md §4.1, §4.3).

After a correct password, an MFA user is NOT logged in: the session only holds a short-lived
"pre-auth" record bound to the user's current password hash. Completing MFA logs the user in
(rotating the session key again) and stamps ``mfa_verified_at``; SessionPolicyMiddleware refuses
any session of an MFA-required user that lacks that stamp.
"""

from __future__ import annotations

import time

from django.contrib.auth import login
from django.utils.crypto import constant_time_compare

from apps.accounts.models import User, UserSession

PREAUTH_KEY = "preauth"
PREAUTH_TTL = 10 * 60
AUTH_TIME_KEY = "auth_time"
LAST_ACTIVITY_KEY = "last_activity"
MFA_VERIFIED_KEY = "mfa_verified_at"
REAUTH_KEY = "reauth_at"
REAUTH_TTL = 5 * 60


def start_preauth(request, user: User, next_url: str = "") -> None:
    request.session.cycle_key()
    request.session[PREAUTH_KEY] = {
        "uid": str(user.pk),
        "hash": user.get_session_auth_hash(),
        "ts": int(time.time()),
        "attempts": 0,
        "next": next_url,
        "enroll_ok": False,
    }


def preauth_user(request) -> User | None:
    data = request.session.get(PREAUTH_KEY)
    if not isinstance(data, dict):
        return None
    if int(time.time()) - int(data.get("ts", 0)) > PREAUTH_TTL:
        clear_preauth(request)
        return None
    user = User.objects.filter(pk=data.get("uid"), is_active=True).first()
    if user is None or not constant_time_compare(user.get_session_auth_hash(), data.get("hash", "")):
        clear_preauth(request)  # password changed or account deactivated since step one
        return None
    return user


def preauth_data(request) -> dict:
    return request.session.get(PREAUTH_KEY) or {}


def update_preauth(request, **changes) -> None:
    data = dict(request.session.get(PREAUTH_KEY) or {})
    data.update(changes)
    request.session[PREAUTH_KEY] = data


def clear_preauth(request) -> None:
    request.session.pop(PREAUTH_KEY, None)


def complete_login(request, user: User, *, mfa_verified: bool) -> None:
    clear_preauth(request)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")  # rotates the session key
    request.session.save()
    UserSession.objects.update_or_create(session_key=request.session.session_key, defaults={"user": user})
    now = int(time.time())
    request.session[AUTH_TIME_KEY] = now
    request.session[LAST_ACTIVITY_KEY] = now
    if mfa_verified:
        request.session[MFA_VERIFIED_KEY] = now


def end_all_sessions(user: User, *, except_key: str | None = None) -> int:
    from django.contrib.sessions.models import Session

    keys = list(UserSession.objects.filter(user=user).exclude(session_key=except_key or "").values_list(
        "session_key", flat=True))
    Session.objects.filter(session_key__in=keys).delete()
    UserSession.objects.filter(session_key__in=keys).delete()
    return len(keys)


def mark_reauthenticated(request) -> None:
    request.session[REAUTH_KEY] = int(time.time())


def recently_reauthenticated(request) -> bool:
    return int(time.time()) - int(request.session.get(REAUTH_KEY, 0)) <= REAUTH_TTL

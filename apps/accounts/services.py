"""Login orchestration (ARCHITECTURE.md §4.1).

Every failure cause (unknown identifier, wrong password, inactive account) produces the same
outcome, message, status and comparable timing. Throttling is keyed on the identifier's HMAC, so
it behaves identically for existing and non-existing accounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from django.contrib.auth.hashers import make_password
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts import devices, mfa, sessions
from apps.accounts.models import User, normalise_identifier
from apps.accounts.throttle import LoginThrottle
from apps.core.audit import record_audit_event, record_security_event
from apps.core.context import RequestContext
from apps.core.models import SecurityEventType

# Hash computed once; verifying against it costs the same as a real password check.
_DUMMY_HASH = make_password("timing-equalisation-only")  # noqa: S106 - not a credential


class LoginOutcome(Enum):
    SUCCESS = "success"
    MFA_VERIFY = "mfa_verify"
    MFA_ENROLL = "mfa_enroll"
    INVALID = "invalid"
    THROTTLED = "throttled"


@dataclass
class LoginResult:
    outcome: LoginOutcome
    user: User | None = None
    retry_after: int = 0


def resolve_identifier(identifier: str) -> User | None:
    identifier = normalise_identifier(identifier)
    if not identifier or len(identifier) > 254:
        return None
    if "@" in identifier:
        return User.objects.filter(email__iexact=identifier).first()
    return User.objects.filter(username__iexact=identifier).first()


def safe_next_url(request, candidate: str | None) -> str:
    """Only same-host, relative paths are honoured (no open redirect, audit T17)."""
    if (
        candidate
        and candidate.startswith("/")
        and not candidate.startswith("//")
        and "\\" not in candidate
        and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()},
                                            require_https=request.is_secure())
    ):
        return candidate
    return ""


def attempt_login(request, identifier: str, password: str, next_url: str = "") -> LoginResult:
    """Step one. Raises ratelimit.LimiterUnavailable if throttling cannot be enforced (fail closed)."""
    ctx = RequestContext.from_request(request)
    user = resolve_identifier(identifier)
    device = devices.valid_device_for(request, user)
    throttle = LoginThrottle(identifier, ctx.ip, str(device.pk) if device else None)

    decision = throttle.check()
    if not decision.allowed:
        record_security_event(SecurityEventType.THROTTLED, ctx=ctx, identifier=identifier,
                              details={"stage": "login", "retry_after": decision.retry_after})
        return LoginResult(LoginOutcome.THROTTLED, retry_after=decision.retry_after)

    if user is None:
        User(password=_DUMMY_HASH).check_password(password)
        ok = False
    else:
        ok = user.check_password(password) and user.is_active
    if not ok:
        throttle.failure()
        record_security_event(SecurityEventType.LOGIN_FAILURE, ctx=ctx, identifier=identifier,
                              user=user if user is not None and user.is_active else None)
        return LoginResult(LoginOutcome.INVALID)

    throttle.success()
    if mfa.is_mfa_enforced(user):
        sessions.start_preauth(request, user, next_url=next_url)
        record_security_event(SecurityEventType.MFA_CHALLENGE, ctx=ctx, user=user)
        if mfa.confirmed_device(user):
            return LoginResult(LoginOutcome.MFA_VERIFY, user)
        return LoginResult(LoginOutcome.MFA_ENROLL, user)

    finish_login(request, user, mfa_verified=False)
    return LoginResult(LoginOutcome.SUCCESS, user)


def finish_login(request, user: User, *, mfa_verified: bool, method: str = "password") -> None:
    ctx = RequestContext.from_request(request)
    sessions.complete_login(request, user, mfa_verified=mfa_verified)
    record_security_event(SecurityEventType.LOGIN_SUCCESS, ctx=ctx, user=user, details={"method": method})
    record_audit_event(user, "AUTH.LOGIN", user, ctx=ctx, changes={"method": method})
    request._issue_device_cookie_for = user  # set on the response by the view

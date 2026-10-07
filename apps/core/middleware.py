"""Request context, session policy and security headers."""

from __future__ import annotations

import time
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.capabilities import Role


class RequestContextMiddleware:
    """Assigns a request id and caches client metadata used by audit records and logs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.core.net import get_client_ip, get_user_agent

        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        # Accept a proxy-supplied id only if it is a short plain token; otherwise generate one.
        request.request_id = incoming if 0 < len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        request.client_ip = get_client_ip(request)
        request.client_user_agent = get_user_agent(request)
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response


class SessionPolicyMiddleware:
    """Idle and absolute session lifetimes, the MFA gate and forced password changes (§4.2, §4.3).

    Defence in depth: even if a view forgot a check, an MFA-required user's session that has not
    completed MFA is refused here.
    """

    EXEMPT_PREFIXES = ("/static/", "/healthz")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith(self.EXEMPT_PREFIXES):
            response = self._enforce(request, user)
            if response is not None:
                return response
        return self.get_response(request)

    def _limits(self, user) -> tuple[int, int]:
        if user.role in Role.ADMINS:
            return settings.SESSION_IDLE_TIMEOUT_ADMIN, settings.SESSION_ABSOLUTE_TIMEOUT_ADMIN
        return settings.SESSION_IDLE_TIMEOUT, settings.SESSION_ABSOLUTE_TIMEOUT

    def _end(self, request, user, reason: str):
        from apps.core.audit import record_security_event
        from apps.core.context import RequestContext
        from apps.core.models import SecurityEventType

        record_security_event(SecurityEventType.SESSION_EXPIRED, ctx=RequestContext.from_request(request), user=user,
                              details={"reason": reason})
        logout(request)
        if reason != "mfa_missing":
            messages.info(request, "Your session has ended. Please sign in again.")
        return redirect(f"{reverse('accounts:login')}")

    def _enforce(self, request, user):
        from apps.accounts import mfa, sessions

        now = int(time.time())
        idle, absolute = self._limits(user)
        session = request.session
        auth_time = int(session.get(sessions.AUTH_TIME_KEY, 0))
        last = int(session.get(sessions.LAST_ACTIVITY_KEY, 0))
        if not auth_time or now - auth_time > absolute:
            return self._end(request, user, "absolute_timeout")
        if not last or now - last > idle:
            return self._end(request, user, "idle_timeout")
        if mfa.is_mfa_enforced(user) and not session.get(sessions.MFA_VERIFIED_KEY):
            return self._end(request, user, "mfa_missing")
        session[sessions.LAST_ACTIVITY_KEY] = now

        if user.must_change_password:
            allowed = {reverse("accounts:password_change"), reverse("accounts:logout")}
            if request.path not in allowed:
                return redirect("accounts:password_change")
        return None


class SecurityHeadersMiddleware:
    """CSP and related headers. No inline script anywhere; styles and scripts from 'self' only."""

    CSP = "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ])

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            # Authenticated pages may contain personal data: never cache them in shared caches or history.
            response.setdefault("Cache-Control", "no-store, private")
        return response

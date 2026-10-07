"""Request context, session policy and security headers."""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.capabilities import Role

CURRENT_REQUEST_ID: ContextVar[str] = ContextVar("current_request_id", default="")


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
        token = CURRENT_REQUEST_ID.set(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            CURRENT_REQUEST_ID.reset(token)
        response["X-Request-ID"] = request.request_id
        return response


class RequestSizeLimitMiddleware:
    """Refuse oversized bodies before anything reads them (DoS guard, ARCHITECTURE.md T16).

    Django's DATA_UPLOAD_MAX_MEMORY_SIZE does not cover file parts of multipart bodies, so the declared
    Content-Length is checked here; Nginx enforces the same limit in front (client_max_body_size).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > settings.MAX_REQUEST_BYTES:
            from django.http import HttpResponse
            from django.template import loader

            return HttpResponse(loader.get_template("errors/413.html").render({}), status=413)
        return self.get_response(request)


class UnsafeMethodThrottleMiddleware:
    """Coarse per-client cap on state-changing requests (POST/PUT/PATCH/DELETE).

    Fails OPEN when the cache is unavailable (D17: low-risk endpoints); the authentication endpoints have
    their own fail-closed throttles.
    """

    UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in self.UNSAFE and settings.UNSAFE_REQUESTS_PER_MINUTE:
            from apps.core import ratelimit

            user = getattr(request, "user", None)
            who = f"u:{user.pk}" if user is not None and user.is_authenticated else f"ip:{request.client_ip or 'unknown'}"
            count = ratelimit.increment(f"unsafe:{who}", 60, fail_closed=False)
            if count > settings.UNSAFE_REQUESTS_PER_MINUTE:
                from django.shortcuts import render

                from apps.core.audit import record_security_event
                from apps.core.context import RequestContext
                from apps.core.models import SecurityEventType

                if count == settings.UNSAFE_REQUESTS_PER_MINUTE + 1:  # record once per window, not per request
                    record_security_event(SecurityEventType.RATE_LIMITED, ctx=RequestContext.from_request(request),
                                          user=user if user is not None and user.is_authenticated else None,
                                          details={"scope": "unsafe_methods"})
                response = render(request, "errors/429.html", status=429)
                response["Retry-After"] = "60"
                return response
        return self.get_response(request)


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

    CSP_DIRECTIVES = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "media-src 'none'",
        "worker-src 'none'",
        "manifest-src 'self'",
        "object-src 'none'",
        "frame-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
    ]

    def __init__(self, get_response):
        self.get_response = get_response
        directives = list(self.CSP_DIRECTIVES)
        if not settings.DEBUG:
            directives.append("upgrade-insecure-requests")
        self.csp = "; ".join(directives)

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.csp)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            # Authenticated pages may contain personal data: never cache them in shared caches or history.
            response.setdefault("Cache-Control", "no-store, private")
        return response

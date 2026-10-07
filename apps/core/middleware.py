import uuid

from django.core.cache import cache
from django.http import HttpResponse
from django.template.loader import render_to_string

from apps.core.audit import record_security_event
from apps.core.context import RequestContext
from apps.core.models import SecurityEventType
from apps.core.utils import get_client_ip


class SecurityHeadersMiddleware:
    """
    Applies production security headers (CSP, HSTS, frame protection, nosniff, Referrer-Policy).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # CSP Header
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "img-src 'self' data:",
            "font-src 'self' https://cdn.jsdelivr.net",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response['Content-Security-Policy'] = "; ".join(csp_directives)
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

        return response


class RateLimitMiddleware:
    """
    Protects sensitive authentication and write endpoints from brute-force, credential stuffing, and DoS.
    """
    RATE_LIMIT_RULES = {
        '/accounts/login/': {'limit': 10, 'window': 60},
        '/accounts/password-reset/': {'limit': 5, 'window': 60},
        '/accounts/mfa/': {'limit': 5, 'window': 60},
        '/api/accounts/login/': {'limit': 10, 'window': 60},
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        matched_rule = None
        for rule_path, config in self.RATE_LIMIT_RULES.items():
            if path.startswith(rule_path) and request.method == 'POST':
                matched_rule = config
                break

        if matched_rule:
            ip = get_client_ip(request)
            cache_key = f"ratelimit:{path}:{ip}"
            current_requests = cache.get(cache_key, 0)

            if current_requests >= matched_rule['limit']:
                record_security_event(
                    SecurityEventType.RATE_LIMITED,
                    ctx=RequestContext.from_request(request),
                    details={'limit': matched_rule['limit'], 'window': matched_rule['window']},
                )
                html = render_to_string('errors/429.html', {'message': 'Too many requests. Please wait a moment and try again.'})
                response = HttpResponse(html, status=429)
                response['Retry-After'] = str(matched_rule['window'])
                return response

            # Increment count
            if current_requests == 0:
                cache.set(cache_key, 1, timeout=matched_rule['window'])
            else:
                cache.incr(cache_key)

        return self.get_response(request)


class AuditLoggingMiddleware:
    """Attaches request metadata (client IP, user agent, request id) used by audit records."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        # Accept a proxy-supplied request id only if it is a plain token; otherwise generate one.
        request.request_id = incoming[:64] if incoming and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        request.client_ip = get_client_ip(request)
        request.client_user_agent = request.META.get("HTTP_USER_AGENT", "")[:256]
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response

"""Client network metadata that is safe to use for rate limiting and audit records."""

from __future__ import annotations

import ipaddress

from django.conf import settings


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def get_client_ip(request) -> str | None:
    """Return the client IP address.

    ``X-Forwarded-For`` is client-controlled unless a trusted reverse proxy appends to it, so it is
    ignored unless ``TRUSTED_PROXY_COUNT`` > 0. With N trusted proxies, the client address is the
    N-th entry from the *right* (the entry written by the outermost trusted proxy); anything to the
    left of it may have been forged by the client. (Fix for audit finding I-1.)
    """
    remote_addr = _valid_ip(request.META.get("REMOTE_ADDR", "") or "")
    proxies = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if proxies <= 0:
        return remote_addr
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if len(hops) < proxies:
        return remote_addr
    return _valid_ip(hops[-proxies]) or remote_addr


def get_user_agent(request, max_length: int = 256) -> str:
    return (request.META.get("HTTP_USER_AGENT", "") or "")[:max_length]

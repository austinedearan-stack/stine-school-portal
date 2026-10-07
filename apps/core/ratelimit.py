"""Cache-backed counters, locks and back-off for throttling (ARCHITECTURE.md D7, D17).

Counters live in the default cache (Redis in production, so every worker shares them). Callers
that guard authentication use ``fail_closed=True``: if the cache is unavailable they get
``LimiterUnavailable`` and must refuse the request (HTTP 503) rather than silently stop limiting.
"""

from __future__ import annotations

import logging
import time

from django.core.cache import cache

logger = logging.getLogger("portal.security")


class LimiterUnavailable(Exception):
    """The rate-limit store could not be reached."""


def _guard(fail_closed: bool, default):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except LimiterUnavailable:
                raise
            except Exception as exc:  # any cache backend error
                logger.error("rate limiter unavailable", extra={"error": type(exc).__name__})
                if fail_closed:
                    raise LimiterUnavailable from exc
                return default

        return wrapper

    return decorator


def _key(*parts: str) -> str:
    return "rl:" + ":".join(parts)


def count(name: str, *, fail_closed: bool = True) -> int:
    return _guard(fail_closed, 0)(lambda: int(cache.get(_key(name)) or 0))()


def increment(name: str, window_seconds: int, *, fail_closed: bool = True) -> int:
    """Increment a fixed-window counter that expires ``window_seconds`` after its first hit."""

    def _incr():
        key = _key(name)
        if cache.add(key, 1, timeout=window_seconds):
            return 1
        try:
            return int(cache.incr(key))
        except ValueError:  # expired between add() and incr()
            cache.add(key, 1, timeout=window_seconds)
            return 1

    return _guard(fail_closed, 0)(_incr)()


def reset(name: str, *, fail_closed: bool = False) -> None:
    _guard(fail_closed, None)(lambda: cache.delete(_key(name)))()


def lock(name: str, seconds: int, *, fail_closed: bool = True) -> None:
    _guard(fail_closed, None)(lambda: cache.set(_key("lock", name), int(time.time()) + seconds, timeout=seconds))()


def locked_for(name: str, *, fail_closed: bool = True) -> int:
    """Seconds remaining on a lock (0 if not locked)."""

    def _remaining():
        until = cache.get(_key("lock", name))
        return max(0, int(until) - int(time.time())) if until else 0

    return _guard(fail_closed, 0)(_remaining)()


def take_slot(name: str, interval_seconds: int, *, fail_closed: bool = True) -> bool:
    """Allow at most one action per ``interval_seconds`` (True = slot taken, proceed)."""
    return _guard(fail_closed, True)(lambda: bool(cache.add(_key("slot", name), 1, timeout=interval_seconds)))()


def backoff_seconds(failures: int, *, threshold: int, base_seconds: int = 60, cap_seconds: int = 1800) -> int:
    """0 below the threshold, then base, 2*base, 4*base ... capped (1, 2, 4 ... 30 minutes by default)."""
    if failures < threshold:
        return 0
    return min(cap_seconds, base_seconds * (2 ** (failures - threshold)))

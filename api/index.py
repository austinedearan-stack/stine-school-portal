"""Vercel entry point for the temporary test deployment (DEPLOYMENT.md §10).

Vercel's Python runtime serves the WSGI callable named ``app``. If start-up fails, every request
gets a short plain-text 503 instead of an opaque crash, so a misconfigured *test* deployment can be
diagnosed without the Vercel logs:

* ImproperlyConfigured (a missing DJANGO_SECRET_KEY or DATABASE_URL): the message, which names the
  setting but never contains a value;
* anything else: only the exception type and message (no traceback, no environment).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal_config.settings.vercel")


def _unavailable(message: str):
    body = f"Portal not available: {message}\n".encode()

    def unavailable_app(environ, start_response):
        start_response("503 Service Unavailable", [("Content-Type", "text/plain; charset=utf-8"),
                                                   ("Cache-Control", "no-store")])
        return [body]

    return unavailable_app


def _build_app():
    try:
        from django.core.exceptions import ImproperlyConfigured
        from django.core.wsgi import get_wsgi_application
    except ImportError as exc:  # dependencies not installed
        return _unavailable(f"start-up failed: {type(exc).__name__}: {exc}")
    try:
        return get_wsgi_application()
    except ImproperlyConfigured as exc:
        return _unavailable(f"not configured: {exc}")
    except Exception as exc:  # noqa: BLE001 - reported as a 503 so the test deployment can be diagnosed
        return _unavailable(f"start-up failed: {type(exc).__name__}: {str(exc)[:300]}")


# Plain top-level assignment: Vercel's builder looks for a module-level ``app``.
app = _build_app()

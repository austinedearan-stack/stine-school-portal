"""Vercel entry point for the temporary test deployment (DEPLOYMENT.md §10).

Vercel's Python runtime serves the WSGI callable named ``app``. If the settings refuse to start
(ImproperlyConfigured: a missing DJANGO_SECRET_KEY or DATABASE_URL), every request gets a 503 that
names the missing setting, so a misconfigured test deployment can be diagnosed without the Vercel
logs. Those messages never contain values. Any other start-up error stays a generic 500.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal_config.settings.vercel")

from django.core.exceptions import ImproperlyConfigured  # noqa: E402


def _misconfigured(message: str):
    body = f"Portal not configured: {message}\n".encode()

    def app(environ, start_response):
        start_response("503 Service Unavailable", [("Content-Type", "text/plain; charset=utf-8"),
                                                   ("Cache-Control", "no-store")])
        return [body]

    return app


def _build_app():
    try:
        from django.core.wsgi import get_wsgi_application

        return get_wsgi_application()
    except ImproperlyConfigured as exc:
        return _misconfigured(str(exc))


# Plain top-level assignment: Vercel's builder looks for a module-level ``app``.
app = _build_app()

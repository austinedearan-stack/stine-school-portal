"""Temporary TEST deployment on Vercel (DEPLOYMENT.md §10). Not the production configuration.

Vercel runs the portal as a short-lived serverless function: no persistent disk, no background
processes, no shared cache, no Nginx. Consequences, accepted for testing with fake data only:

* uploads are written to /tmp and disappear when the function instance is recycled;
* rate-limit counters live in each instance's memory, so throttling is per instance, not global;
* scheduled jobs (audit seals, backups, hostel offer expiry, outbox) do not run;
* e-mail (password-reset codes, notifications) is written to the function log;
* request bodies are limited to 4.5 MB by Vercel.

The database is a managed PostgreSQL (for example Neon, from the Vercel Marketplace) given as
``DATABASE_URL`` (the unpooled URL is preferred when available).
"""

import base64
import hashlib
import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import SECRET_KEY, with_audit_alias
from .env import env_list

DEBUG = False

if not SECRET_KEY or len(SECRET_KEY) < 50 or "insecure" in SECRET_KEY.lower():
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a random value of at least 50 characters.")

# --- Hosts: the deployment's own *.vercel.app names (system variables Vercel sets) plus any extras --
_vercel_hosts = [os.environ.get(name, "") for name in ("VERCEL_URL", "VERCEL_BRANCH_URL",
                                                       "VERCEL_PROJECT_PRODUCTION_URL")]
ALLOWED_HOSTS = sorted({host for host in _vercel_hosts if host} | set(env_list("DJANGO_ALLOWED_HOSTS")))
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]

# Test deployments may derive the MFA/HMAC keys from the secret key (as development does).
if not MFA_ENCRYPTION_KEYS:  # noqa: F405
    MFA_ENCRYPTION_KEYS = [base64.urlsafe_b64encode(hashlib.sha256(b"mfa:" + SECRET_KEY.encode()).digest()).decode()]
if not PORTAL_HMAC_KEY:  # noqa: F405
    PORTAL_HMAC_KEY = hashlib.sha256(b"hmac:" + SECRET_KEY.encode()).hexdigest()


# --- Database -----------------------------------------------------------------------------------
def database_from_url(url: str) -> dict:
    """A postgres:// connection URL (credentials, host, port, name, query options) -> Django settings (TLS required)."""
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL must be a postgres:// URL.")
    options = dict(parse_qsl(parts.query))
    options["sslmode"] = options.get("sslmode", "require")
    if options["sslmode"] in {"disable", "allow", "prefer"}:
        raise ImproperlyConfigured("DATABASE_URL must use TLS (sslmode=require or stricter).")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or 5432),
        "CONN_MAX_AGE": 0,  # serverless: no long-lived connections
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }


def find_database_url(environ) -> str:
    """The first URL found, direct (unpooled) connections first.

    Vercel's Neon integration may prefix its variables (e.g. ``STORAGE_DATABASE_URL``), so names are
    matched by suffix as well as exactly.
    """
    for suffix in ("DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING", "DATABASE_URL", "POSTGRES_URL"):
        for name in sorted(environ):
            if (name == suffix or name.endswith("_" + suffix)) and environ[name].strip():
                return environ[name].strip()
    return ""


_url = find_database_url(os.environ)
if not _url:
    raise ImproperlyConfigured(
        "Set DATABASE_URL (managed PostgreSQL, e.g. Vercel Storage -> Neon) for the Vercel test deployment."
    )
DATABASES = with_audit_alias(database_from_url(_url))

# --- Cache, files, mail ---------------------------------------------------------------------------
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "portal-vercel"}}
MEDIA_ROOT = Path("/tmp/private-media")  # noqa: S108 # nosec B108 - only writable path on Vercel; ephemeral
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# Static files are served by WhiteNoise straight from the source directories (no collectstatic step).
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = False

# --- HTTPS behind Vercel's edge (it always terminates TLS and sets X-Forwarded-For/-Proto) ---------
TRUSTED_PROXY_COUNT = 1
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = False  # the edge already redirects to HTTPS
SECURE_HSTS_SECONDS = 3600
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_NAME = "__Host-portal_session"
CSRF_COOKIE_NAME = "__Host-portal_csrf"
DEVICE_COOKIE_NAME = "__Host-portal_device"
DJANGO_ADMIN_ENABLED = False

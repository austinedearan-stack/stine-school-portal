"""Local development settings. Never use in production."""

import base64
import hashlib

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import SECRET_KEY, database_from_env, with_audit_alias
from .env import env_bool, env_list, env_str

DEBUG = env_bool("DJANGO_DEBUG", True)

if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is not set. Copy .env.example to .env and generate a key with:\n"
        "  python -c \"from django.core.management.utils import get_random_secret_key as g; print(g())\""
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])

# Development convenience only: derive the MFA/HMAC keys from the dev SECRET_KEY when unset so a
# fresh checkout works. Production requires real, independent values (production.py).
if not MFA_ENCRYPTION_KEYS:  # noqa: F405
    MFA_ENCRYPTION_KEYS = [base64.urlsafe_b64encode(hashlib.sha256(b"mfa:" + SECRET_KEY.encode()).digest()).decode()]
if not PORTAL_HMAC_KEY:  # noqa: F405
    PORTAL_HMAC_KEY = hashlib.sha256(b"hmac:" + SECRET_KEY.encode()).hexdigest()

# SQLite is acceptable for quick local work; PostgreSQL (docker compose) is recommended and is
# required for the concurrency tests.
DATABASES = with_audit_alias(database_from_env())

CACHES = {
    "default": (
        {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env_str("REDIS_URL")}
        if env_str("REDIS_URL")
        else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "portal-dev"}
    )
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

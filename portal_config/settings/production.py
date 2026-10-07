"""Production settings.

Fails fast (ImproperlyConfigured) on any unsafe or missing configuration instead of silently
falling back to insecure defaults.
"""

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import (
    ALLOWED_HOSTS,
    DJANGO_ADMIN_ENABLED,
    MFA_ENCRYPTION_KEYS,
    PORTAL_HMAC_KEY,
    SECRET_KEY,
    TRUSTED_PROXY_COUNT,
    database_from_env,
    with_audit_alias,
)
from .env import env_bool, env_int, env_list, env_str

DEBUG = False

# --- Secrets & hosts ------------------------------------------------------------------------------
if not SECRET_KEY or len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 10 or "insecure" in SECRET_KEY.lower():
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a random value of at least 50 characters.")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the exact production host names (no '*').")
if DJANGO_ADMIN_ENABLED:
    raise ImproperlyConfigured(
        "DJANGO_ADMIN_ENABLED must not be set in production: the stock admin bypasses MFA, policies and audit."
    )

if not MFA_ENCRYPTION_KEYS:
    raise ImproperlyConfigured("MFA_ENCRYPTION_KEYS must list at least one Fernet key (see .env.example).")
for _key in MFA_ENCRYPTION_KEYS:
    try:
        Fernet(_key.encode())
    except (ValueError, TypeError) as _exc:
        raise ImproperlyConfigured("MFA_ENCRYPTION_KEYS contains an invalid Fernet key.") from _exc
if not PORTAL_HMAC_KEY or len(PORTAL_HMAC_KEY) < 32 or PORTAL_HMAC_KEY == SECRET_KEY:
    raise ImproperlyConfigured(
        "PORTAL_HMAC_KEY must be a random value of at least 32 characters, independent of DJANGO_SECRET_KEY."
    )

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
for origin in CSRF_TRUSTED_ORIGINS:
    if not origin.startswith("https://"):
        raise ImproperlyConfigured("DJANGO_CSRF_TRUSTED_ORIGINS entries must be https:// origins.")

# --- Database: PostgreSQL only, encrypted by default, never the superuser -------------------------
DATABASES = with_audit_alias(database_from_env())
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("Production requires PostgreSQL (DB_ENGINE=postgresql).")
if not DATABASES["default"].get("PASSWORD"):
    raise ImproperlyConfigured("DB_PASSWORD is required in production.")
if DATABASES["default"]["USER"] in {"postgres", "root", "admin"}:
    raise ImproperlyConfigured("The application must not connect as a PostgreSQL superuser account.")
DATABASES["default"]["OPTIONS"]["sslmode"] = env_str("DB_SSLMODE", "require")
if DATABASES["default"]["OPTIONS"]["sslmode"] in {"disable", "allow", "prefer"} and not env_bool(
    "DB_ALLOW_INSECURE_TRANSPORT", False
):
    raise ImproperlyConfigured(
        "DB_SSLMODE must be require/verify-ca/verify-full. Set DB_ALLOW_INSECURE_TRANSPORT=true only when the "
        "database is reachable solely over a private network (e.g. the compose-internal network)."
    )

# --- Cache / rate limiting: Redis is mandatory (per-process caches cannot enforce limits) ----------
REDIS_URL = env_str("REDIS_URL", required=True)
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}

# --- HTTPS, cookies, headers ----------------------------------------------------------------------
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31_536_000, minimum=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)  # opt in deliberately; preload is hard to undo
# W021 only says "preload not enabled"; that is a documented deliberate choice (DEPLOYMENT.md).
SILENCED_SYSTEM_CHECKS = [] if SECURE_HSTS_PRELOAD else ["security.W021"]
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]  # internal container health probe over plain HTTP

# Only trust X-Forwarded-Proto when a known reverse proxy sits in front (it overwrites the header).
if TRUSTED_PROXY_COUNT > 0:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_NAME = "__Host-portal_session"
CSRF_COOKIE_NAME = "__Host-portal_csrf"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Email ----------------------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env_str("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587, minimum=1, maximum=65535)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 10
SERVER_EMAIL = env_str("SERVER_EMAIL", "no-reply@example.test")

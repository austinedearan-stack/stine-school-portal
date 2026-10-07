"""Local development settings. Never use in production."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import SECRET_KEY, database_from_env
from .env import env_bool, env_list, env_str

DEBUG = env_bool("DJANGO_DEBUG", True)

if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is not set. Copy .env.example to .env and generate a key with:\n"
        "  python -c \"from django.core.management.utils import get_random_secret_key as g; print(g())\""
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])

# SQLite is acceptable for quick local work; PostgreSQL (docker compose) is recommended and is
# required for the concurrency tests.
DATABASES = {"default": database_from_env()}

CACHES = {
    "default": (
        {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env_str("REDIS_URL")}
        if env_str("REDIS_URL")
        else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "portal-dev"}
    )
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

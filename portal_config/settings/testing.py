"""Settings for the automated test suite.

Uses PostgreSQL when DB_ENGINE=postgresql is provided (CI and concurrency tests), otherwise an
in-memory SQLite database. The secret key is random per run: no secret is stored in the repo.
"""

import os

from django.core.management.utils import get_random_secret_key

from .base import *  # noqa: F403
from .base import database_from_env

SECRET_KEY = get_random_secret_key()
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

if os.environ.get("DB_ENGINE", "sqlite").lower() == "postgresql":
    DATABASES = {"default": database_from_env()}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Fast hasher keeps the suite quick; Argon2 configuration is asserted separately in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # test-only fast hasher

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "portal-test"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MEDIA_ROOT = BASE_DIR / "var" / "test-private-media"  # noqa: F405
DJANGO_ADMIN_ENABLED = False

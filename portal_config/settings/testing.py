"""Settings for the automated test suite.

Uses PostgreSQL when DB_ENGINE=postgresql is provided (CI and concurrency tests), otherwise an
in-memory SQLite database. The secret key is random per run: no secret is stored in the repo.
"""

import os
import secrets

from cryptography.fernet import Fernet
from django.core.management.utils import get_random_secret_key

from .base import *  # noqa: F403
from .base import database_from_env

SECRET_KEY = get_random_secret_key()
MFA_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
PORTAL_HMAC_KEY = secrets.token_urlsafe(48)
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

if os.environ.get("DB_ENGINE", "sqlite").lower() == "postgresql":
    DATABASES = {"default": database_from_env()}
    # Lets the test runner (as table owner) flush append-only tables between transactional tests.
    # Tests of the append-only trigger switch it off with SET LOCAL. See apps/core/db_guards.py.
    DATABASES["default"]["OPTIONS"]["options"] = "-c portal.audit_maintenance=on"
    # Independent connection for denial/security records (D16). Off by default in tests; a test opts in with
    # @override_settings(AUDIT_INDEPENDENT_CONNECTION=True) and django_db(databases=["default", "audit"]).
    DATABASES["audit"] = {**DATABASES["default"], "TEST": {"MIRROR": "default"}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Fast hasher keeps the suite quick; Argon2 configuration is asserted separately in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # test-only fast hasher

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "portal-test"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_SEND_SYNC = True  # tests inspect mail.outbox synchronously
MEDIA_ROOT = BASE_DIR / "var" / "test-private-media"  # noqa: F405
DJANGO_ADMIN_ENABLED = False

AUDIT_INDEPENDENT_CONNECTION = False

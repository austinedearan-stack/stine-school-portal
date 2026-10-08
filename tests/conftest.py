import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent


def run_python(code: str, env_overrides: dict, settings_module: str) -> subprocess.CompletedProcess:
    """Import settings in a clean subprocess with a fully controlled environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DB_", "REDIS", "SECURE_", "TRUSTED_"))}
    env.update({"DJANGO_SETTINGS_MODULE": settings_module, "PYTHONPATH": str(ROOT)})
    env.update(env_overrides)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def py():
    return run_python


# A syntactically valid, random-looking value used only inside these subprocess tests.
STRONG_TEST_KEY = "t3st-only-" + "".join(chr(97 + (i * 7) % 26) + str(i % 10) for i in range(30))

PROD_ENV = {
    "DJANGO_SECRET_KEY": STRONG_TEST_KEY,
    "DJANGO_ALLOWED_HOSTS": "portal.example.test",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://portal.example.test",
    "DB_ENGINE": "postgresql",
    "DB_USER": "portal_app",
    "DB_PASSWORD": "unused-in-settings-import",  # secret-scan: allow
    "REDIS_URL": "redis://localhost:6379/0",
    # Generated per test run; never a stored value.
    "MFA_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
    "PORTAL_HMAC_KEY": secrets.token_urlsafe(48),
}


def pytest_collection_modifyitems(config, items):
    """Skip tests marked ``postgres`` unless the suite runs against PostgreSQL."""
    from django.conf import settings

    if settings.DATABASES["default"]["ENGINE"].endswith("postgresql"):
        return
    skip = pytest.mark.skip(reason="requires PostgreSQL (DB_ENGINE=postgresql)")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _reference_data(request, _django_db_marker):
    """Restore rows created by data migrations (capability groups, request categories).

    A transactional test ends by flushing every table, which also deletes these rows; any later test
    (or the next run with --reuse-db) would then see an empty catalogue. Re-seeding only when missing
    keeps results independent of test order at the cost of two existence queries.
    """
    if request.node.get_closest_marker("django_db") is None and not {"db", "transactional_db"} & set(
        request.fixturenames
    ):
        return
    request.getfixturevalue("_django_db_helper")
    import importlib

    from django.apps import apps
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType

    from apps.core.capabilities import sync_capability_groups
    from apps.student_requests.models import RequestCategory

    if not Group.objects.exists():
        sync_capability_groups(Group, Permission, ContentType)
    if not RequestCategory.objects.exists():
        importlib.import_module("apps.student_requests.migrations.0002_history_guard_and_categories").seed_categories(
            apps, None
        )

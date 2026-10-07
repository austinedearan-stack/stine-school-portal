import os
import subprocess
import sys
from pathlib import Path

import pytest

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
}

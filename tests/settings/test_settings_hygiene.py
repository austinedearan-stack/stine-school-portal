"""Settings hygiene: no hard-coded secrets, safe defaults (spec §23; audit A-3, S-4, I-6)."""

import re
from pathlib import Path

from django.conf import settings

from tests.conftest import ROOT

SETTINGS_DIR = ROOT / "portal_config" / "settings"


def test_no_secret_literals_in_settings():
    for path in SETTINGS_DIR.glob("*.py"):
        text = path.read_text()
        assert not re.search(r"SECRET_KEY\s*=\s*['\"]", text), f"literal SECRET_KEY in {path.name}"
        assert "insecure-default" not in text


def test_development_settings_require_secret_key(py):
    result = py("import django; django.setup()", {}, "portal_config.settings.development")
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY is not set" in result.stderr


def test_wsgi_and_asgi_default_to_production():
    for name in ("wsgi.py", "asgi.py"):
        text = (ROOT / "portal_config" / name).read_text()
        assert '"portal_config.settings.production"' in text


def test_apps_directory_not_injected_into_sys_path():
    # The old settings put apps/ on sys.path, making `import requests` resolve to the portal app.
    import sys

    assert str(ROOT / "apps") not in sys.path
    assert "sys.path.insert" not in (SETTINGS_DIR / "base.py").read_text()


def test_password_hashing_uses_argon2_in_real_settings():
    text = (SETTINGS_DIR / "base.py").read_text()
    first = re.search(r"PASSWORD_HASHERS = \[\s*\"([^\"]+)\"", text).group(1)
    assert first.endswith("Argon2PasswordHasher")


def test_password_validators_enforce_min_and_max_length():
    names = [v["NAME"] for v in settings.AUTH_PASSWORD_VALIDATORS]
    assert any(n.endswith("MinimumLengthValidator") for n in names)
    assert any(n.endswith("MaximumLengthValidator") for n in names)


def test_csrf_cookie_is_httponly_and_session_cookie_httponly():
    assert settings.CSRF_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_HTTPONLY is True


def test_env_example_contains_placeholders_only():
    text = (ROOT / ".env.example").read_text()
    for line in text.splitlines():
        if re.match(r"^[A-Z_]*(SECRET|PASSWORD)[A-Z_]*=", line):
            value = line.split("=", 1)[1]
            assert value.startswith("<") and value.endswith(">"), line


def test_env_file_is_gitignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored


def test_media_root_is_outside_static_and_templates():
    media = Path(settings.MEDIA_ROOT).resolve()
    assert Path(settings.STATIC_ROOT).resolve() not in media.parents
    assert (ROOT / "static").resolve() not in media.parents

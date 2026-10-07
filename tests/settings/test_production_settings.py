"""Production settings must fail fast on unsafe configuration (spec §23, §29, §30; audit A-3, I-7)."""

import json

import pytest

from tests.conftest import PROD_ENV

PROD = "portal_config.settings.production"
DUMP = (
    "import json, django; from django.conf import settings as s; django.setup(); print(json.dumps({"
    "'DEBUG': s.DEBUG, 'SESSION_COOKIE_SECURE': s.SESSION_COOKIE_SECURE, 'CSRF_COOKIE_SECURE': s.CSRF_COOKIE_SECURE,"
    "'CSRF_COOKIE_HTTPONLY': s.CSRF_COOKIE_HTTPONLY, 'SESSION_COOKIE_HTTPONLY': s.SESSION_COOKIE_HTTPONLY,"
    "'HSTS': s.SECURE_HSTS_SECONDS, 'SSL_REDIRECT': s.SECURE_SSL_REDIRECT, 'NOSNIFF': s.SECURE_CONTENT_TYPE_NOSNIFF,"
    "'XFO': s.X_FRAME_OPTIONS, 'SESSION_NAME': s.SESSION_COOKIE_NAME, 'CSRF_NAME': s.CSRF_COOKIE_NAME,"
    "'SSLMODE': s.DATABASES['default']['OPTIONS']['sslmode'], 'CACHE': s.CACHES['default']['BACKEND'],"
    "'HASHER': s.PASSWORD_HASHERS[0], 'PROXY_HEADER': getattr(s, 'SECURE_PROXY_SSL_HEADER', None),"
    "'ADMIN': s.DJANGO_ADMIN_ENABLED}))"
)


def test_secure_configuration_loads(py):
    result = py(DUMP, PROD_ENV, PROD)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(result.stdout)
    assert cfg["DEBUG"] is False
    assert cfg["SESSION_COOKIE_SECURE"] and cfg["CSRF_COOKIE_SECURE"]
    assert cfg["SESSION_COOKIE_HTTPONLY"] and cfg["CSRF_COOKIE_HTTPONLY"]
    assert cfg["HSTS"] >= 31_536_000
    assert cfg["SSL_REDIRECT"] is True
    assert cfg["NOSNIFF"] is True and cfg["XFO"] == "DENY"
    assert cfg["SESSION_NAME"].startswith("__Host-") and cfg["CSRF_NAME"].startswith("__Host-")
    assert cfg["SSLMODE"] == "require"
    assert cfg["CACHE"].endswith("RedisCache")
    assert cfg["HASHER"].endswith("Argon2PasswordHasher")
    assert cfg["PROXY_HEADER"] is None  # X-Forwarded-Proto not trusted without a declared proxy
    assert cfg["ADMIN"] is False


@pytest.mark.parametrize(
    "override, message",
    [
        ({"DJANGO_SECRET_KEY": ""}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_SECRET_KEY": "short-key"}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_SECRET_KEY": "django-insecure-" + "a1b2c3d4e5" * 5}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_SECRET_KEY": "a" * 60}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_ALLOWED_HOSTS": ""}, "DJANGO_ALLOWED_HOSTS"),
        ({"DJANGO_ALLOWED_HOSTS": "*"}, "DJANGO_ALLOWED_HOSTS"),
        ({"DJANGO_ADMIN_ENABLED": "true"}, "DJANGO_ADMIN_ENABLED"),
        ({"DB_ENGINE": "sqlite"}, "PostgreSQL"),
        ({"DB_USER": "postgres"}, "superuser"),
        ({"DB_PASSWORD": ""}, "DB_PASSWORD"),
        ({"DB_SSLMODE": "prefer"}, "DB_SSLMODE"),
        ({"REDIS_URL": ""}, "REDIS_URL"),
        ({"DJANGO_CSRF_TRUSTED_ORIGINS": "http://portal.example.test"}, "https"),
        ({"MFA_ENCRYPTION_KEYS": ""}, "MFA_ENCRYPTION_KEYS"),
        ({"MFA_ENCRYPTION_KEYS": "not-a-fernet-key"}, "MFA_ENCRYPTION_KEYS"),
        ({"PORTAL_HMAC_KEY": ""}, "PORTAL_HMAC_KEY"),
        ({"PORTAL_HMAC_KEY": "too-short"}, "PORTAL_HMAC_KEY"),
    ],
)
def test_unsafe_configuration_is_refused(py, override, message):
    env = {**PROD_ENV, **override}
    result = py("import django; django.setup()", env, PROD)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert message in result.stderr


def test_proxy_ssl_header_only_with_trusted_proxy(py):
    result = py(DUMP, {**PROD_ENV, "TRUSTED_PROXY_COUNT": "1"}, PROD)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["PROXY_HEADER"] == ["HTTP_X_FORWARDED_PROTO", "https"]


def test_deploy_checks_pass(py):
    code = (
        "import sys, django; django.setup(); from django.core.management import call_command;"
        "call_command('check', '--deploy', '--fail-level', 'WARNING')"
    )
    result = py(code, PROD_ENV, PROD)
    assert result.returncode == 0, result.stdout + result.stderr

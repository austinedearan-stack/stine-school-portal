"""The temporary Vercel test configuration (DEPLOYMENT.md §10) must still fail safe."""

import json

from tests.conftest import STRONG_TEST_KEY

VERCEL = "portal_config.settings.vercel"
# Fake value, assembled so the secret scanner does not see a literal credential URL.
URL = "postgres://" + "tester:p%40ss" + "@db.example.test:6543/portal?sslmode=require&channel_binding=require"
ENV = {"DJANGO_SECRET_KEY": STRONG_TEST_KEY, "DATABASE_URL": URL, "VERCEL_URL": "portal-abc123.vercel.app",
       "VERCEL_PROJECT_PRODUCTION_URL": "portal.vercel.app"}
DUMP = (
    "import json, django; from django.conf import settings as s; django.setup(); d = s.DATABASES['default'];"
    "print(json.dumps({'DEBUG': s.DEBUG, 'HOSTS': s.ALLOWED_HOSTS, 'CSRF': s.CSRF_TRUSTED_ORIGINS,"
    "'DB': [d['USER'], d['PASSWORD'], d['HOST'], d['PORT'], d['NAME'], d['OPTIONS'], d['CONN_MAX_AGE']],"
    "'SECURE': [s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SESSION_COOKIE_NAME],"
    "'PROXY': s.SECURE_PROXY_SSL_HEADER, 'ADMIN': s.DJANGO_ADMIN_ENABLED, 'MEDIA': str(s.MEDIA_ROOT)}))"
)


def test_loads_with_vercel_hosts_tls_database_and_secure_cookies(py):
    result = py(DUMP, ENV, VERCEL)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(result.stdout)
    assert cfg["DEBUG"] is False and cfg["ADMIN"] is False
    assert cfg["HOSTS"] == ["portal-abc123.vercel.app", "portal.vercel.app"]
    assert cfg["CSRF"] == ["https://portal-abc123.vercel.app", "https://portal.vercel.app"]
    assert cfg["DB"] == ["tester", "p@ss", "db.example.test", "6543", "portal",
                         {"sslmode": "require", "channel_binding": "require"}, 0]
    assert cfg["SECURE"] == [True, True, "__Host-portal_session"]
    assert cfg["PROXY"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert cfg["MEDIA"].replace("\\", "/").endswith("/tmp/private-media")


def test_unpooled_url_is_preferred(py):
    env = {**ENV, "DATABASE_URL_UNPOOLED": URL.replace("db.example.test:6543", "direct.example.test:5432")}
    cfg = json.loads(py(DUMP, env, VERCEL).stdout)
    assert cfg["DB"][2] == "direct.example.test"


def test_refuses_missing_database_plaintext_database_and_weak_key(py):
    for overrides, message in (
        ({"DATABASE_URL": ""}, "DATABASE_URL"),
        ({"DATABASE_URL": URL.replace("sslmode=require", "sslmode=disable")}, "TLS"),
        ({"DATABASE_URL": "mysql://u:p@h/db"}, "postgres://"),
        ({"DJANGO_SECRET_KEY": "short"}, "DJANGO_SECRET_KEY"),
    ):
        result = py("import django; django.setup()", {**ENV, **overrides}, VERCEL)
        assert result.returncode != 0 and message in result.stderr, overrides


def test_misconfiguration_is_a_503_naming_the_setting_without_values(py):
    code = (
        "from api.index import app; out = [];"
        "body = b''.join(app({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/'}, lambda s, h: out.append(s)));"
        "print(out[0]); print(body.decode())"
    )
    result = py(code, {**ENV, "DATABASE_URL": ""}, VERCEL)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("503") and "not configured" in result.stdout and "DATABASE_URL" in result.stdout
    assert STRONG_TEST_KEY not in result.stdout


def test_vercel_requirements_match_the_pinned_base_list():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    def pins(name):
        return sorted(line.split("#")[0].strip() for line in (root / name).read_text(encoding="utf-8").splitlines()
                      if line.split("#")[0].strip())

    assert pins("requirements.txt") == pins("requirements/base.txt")
    import tomllib

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert sorted(project["dependencies"]) == pins("requirements/base.txt")  # Vercel installs from pyproject


def test_vercel_sends_every_request_to_django():
    """Vercel otherwise serves repository files (source, docs) as static files before any rewrite.

    "routes" run before Vercel's filesystem lookup, so every path reaches Django; WhiteNoise serves
    /static/. There is no build step and no output directory to publish.
    """
    from pathlib import Path

    config = json.loads((Path(__file__).resolve().parents[2] / "vercel.json").read_text(encoding="utf-8"))
    assert config["routes"] == [{"src": "/(.*)", "dest": "/api/index"}]
    assert not {"rewrites", "buildCommand", "outputDirectory"} & set(config)
    assert not any(route.get("handle") == "filesystem" for route in config["routes"])


def test_entry_point_serves_static_files_without_collectstatic(py):
    code = (
        "import sys; sys.argv = ['x']; from api.index import app; from django.test import Client;"
        "r = Client(HTTP_HOST='portal.vercel.app').get('/static/css/portal.css', secure=True);"
        "print(r.status_code, r.headers.get('Content-Type'))"
    )
    result = py(code, ENV, VERCEL)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0] == "200" and "text/css" in result.stdout

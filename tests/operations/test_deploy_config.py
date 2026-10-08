"""Static checks of the deployment configuration (DEPLOYMENT.md). CI additionally runs ``nginx -t`` on the
rendered Nginx config and ``docker compose config`` on the production compose file."""

import re
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(__file__).resolve().parents[2]
NGINX = (ROOT / "deploy/nginx/templates/portal.conf.template").read_text(encoding="utf-8")
PROXY = (ROOT / "deploy/nginx/templates/proxy_params.inc").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def _service(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|^\S)", COMPOSE, re.M | re.S)
    assert match, name
    return match.group(1)


def test_nginx_hides_versions_and_limits_bodies_like_django():
    assert "server_tokens off;" in NGINX
    size = re.search(r"client_max_body_size (\d+)m;", NGINX)
    assert size and int(size.group(1)) * 1024 * 1024 == settings.MAX_REQUEST_BYTES
    assert "autoindex on" not in NGINX


def test_nginx_tls_is_modern_and_unknown_hosts_are_refused():
    protocols = re.search(r"ssl_protocols ([^;]+);", NGINX).group(1).split()
    assert protocols == ["TLSv1.2", "TLSv1.3"]
    assert "ssl_session_tickets off;" in NGINX
    assert "ssl_reject_handshake on;" in NGINX and "return 444;" in NGINX
    assert "return 301 https://$host$request_uri;" in NGINX


def test_nginx_private_files_are_internal_and_match_the_x_accel_prefix():
    block = re.search(r"location /_protected/ \{(.*?)\n    \}", NGINX, re.S).group(1)
    assert "internal;" in block and "alias /srv/private-media/;" in block
    for header in ("X-Content-Type-Options", "Content-Security-Policy", "Cache-Control"):
        assert header in block
    assert "PRIVATE_FILES_X_ACCEL_PREFIX: /_protected/" in COMPOSE
    assert "private_media:/srv/private-media:ro" in COMPOSE


def test_nginx_forwards_exactly_one_client_address():
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in PROXY  # overwritten, not appended
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in PROXY
    assert 'TRUSTED_PROXY_COUNT: "1"' in COMPOSE
    assert re.search(r"location ~ /\\\.\(\?!well-known/\) \{ return 404; \}", NGINX)
    assert "zone=portal_auth" in NGINX


def test_only_nginx_publishes_ports_and_data_network_is_internal():
    assert COMPOSE.count("ports:") == 1 and "ports:" in _service("nginx")
    assert re.search(r"data: \{internal: true\}", COMPOSE)
    for name in ("db", "redis"):
        assert "networks: [data]" in _service(name)


@pytest.mark.parametrize("name", ["migrate", "web", "scheduler", "backup", "nginx", "redis"])
def test_containers_are_hardened(name):
    block = _service(name)
    if name in ("migrate", "web", "scheduler", "backup"):
        assert "<<: *app" in block and "read_only: false" not in block
        block = re.search(r"^x-app: &app\n(.*?)^\S", COMPOSE, re.M | re.S).group(1)
        assert "cap_drop: [ALL]" in block and "no-new-privileges:true" in block
    assert "read_only: true" in block


def test_secrets_are_not_in_the_compose_file_and_owner_credentials_are_isolated():
    assert "PASSWORD:" not in COMPOSE.replace("DB_ALLOW_INSECURE_TRANSPORT", "")
    assert "owner.env" in _service("migrate") and "owner.env" not in _service("web")
    assert "backup.env" in _service("backup") and "backup.env" not in _service("web")


def test_image_runs_gunicorn_config_as_non_root():
    assert 'CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py"' in DOCKERFILE
    assert "USER portal" in DOCKERFILE
    assert DOCKERFILE.rstrip().endswith("FROM app AS runtime")  # default target is the slim runtime
    assert "postgresql-client-16" in DOCKERFILE


def test_gunicorn_config_values():
    namespace = {}
    exec(compile((ROOT / "deploy/gunicorn.conf.py").read_text(encoding="utf-8"), "gunicorn.conf.py", "exec"),  # noqa: S102
         namespace)
    assert namespace["timeout"] <= 60 and namespace["limit_request_fields"] <= 100
    assert "%(U)s" in namespace["access_log_format"] and "%(r)s" not in namespace["access_log_format"]
    assert namespace["worker_tmp_dir"] == "/dev/shm"
    assert 1 <= namespace["workers"] <= 9


def test_env_examples_hold_placeholders_only():
    for path in (ROOT / "deploy/env").glob("*.env.example"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and ("PASSWORD" in line or "KEY" in line.split("=")[0]):
                assert "=<" in line, f"{path.name}: {line}"


def test_scheduled_jobs_cover_every_operational_command():
    from apps.core.jobs import JOBS

    commands = {job.command for job in JOBS}
    assert {"seal_audit_log", "verify_audit_seals", "expire_hostel_offers", "close_stale_requests",
            "publish_due_announcements", "send_outbox", "clearsessions", "backup_portal",
            "restore_test"} <= commands

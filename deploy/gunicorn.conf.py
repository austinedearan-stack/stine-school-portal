"""Gunicorn configuration for production (DEPLOYMENT.md). Loaded with ``gunicorn -c deploy/gunicorn.conf.py``.

Gunicorn listens only on the internal container network; Nginx terminates TLS, enforces body and rate
limits and is the only published service. Values can be tuned with GUNICORN_* environment variables.
"""

import multiprocessing
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")  # noqa: S104 - container-internal network only
workers = _int("GUNICORN_WORKERS", min(2 * multiprocessing.cpu_count() + 1, 9))
worker_class = "sync"
threads = 1

# Slow or stuck requests are killed instead of tying up workers; Nginx buffers slow clients.
timeout = _int("GUNICORN_TIMEOUT", 30)
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically (bounds the impact of any memory growth).
max_requests = 1000
max_requests_jitter = 100

# Request-line and header limits (Nginx enforces stricter ones in front).
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# Heartbeat files in memory: the root filesystem is read-only in production.
worker_tmp_dir = "/dev/shm"  # noqa: S108 - per-container tmpfs, gunicorn heartbeat only

# Access log without query strings: searches and filters may contain personal data.
accesslog = "-"
access_log_format = '%(h)s "%(m)s %(U)s %(H)s" %(s)s %(B)s %(L)ss rid=%({x-request-id}o)s'
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
capture_output = True
proc_name = "school-portal"

# X-Forwarded-* are interpreted by Django (TRUSTED_PROXY_COUNT / SECURE_PROXY_SSL_HEADER), not Gunicorn.
forwarded_allow_ips = "127.0.0.1"

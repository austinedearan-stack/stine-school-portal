# Production-capable image: pinned base, non-root user, no build tools in the final stage.
# Targets: "runtime" (default; web, migrate, scheduler) and "ops" (adds the PostgreSQL 16 client for
# backups and the restore test). Pin the base images by digest in your registry for reproducible builds.
FROM python:3.13-slim-bookworm AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements/ requirements/
RUN pip wheel --wheel-dir /wheels -r requirements/prod.txt

FROM python:3.13-slim-bookworm AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=portal_config.settings.production
RUN groupadd --system portal && useradd --system --gid portal --home-dir /app --shell /usr/sbin/nologin portal
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* && rm -rf /wheels
COPY --chown=portal:portal . /app
# Static files are collected at build time with build-only settings (random key, no DB access).
RUN DJANGO_SETTINGS_MODULE=portal_config.settings.collectstatic python manage.py collectstatic --noinput \
    && mkdir -p /app/var/private-media /backups && chown -R portal:portal /app/var /app/staticfiles /backups
USER portal
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "portal_config.wsgi:application"]

# Backups and restore tests need pg_dump/pg_restore matching the server's major version (16).
FROM app AS ops
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
USER portal
HEALTHCHECK NONE
CMD ["python", "manage.py", "run_jobs", "--group", "backup"]

FROM app AS runtime

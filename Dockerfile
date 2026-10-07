# Production-capable image: pinned base, non-root user, no build tools in the final stage.
FROM python:3.13-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements/ requirements/
RUN pip wheel --wheel-dir /wheels -r requirements/prod.txt

FROM python:3.13-slim
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
    && mkdir -p /app/var/private-media && chown -R portal:portal /app/var /app/staticfiles
USER portal
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["gunicorn", "portal_config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-", "--forwarded-allow-ips", "127.0.0.1"]

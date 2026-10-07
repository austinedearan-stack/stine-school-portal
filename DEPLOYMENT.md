# Deployment

Status: Phase 1 provides the production *settings*, the Docker image definition and the PostgreSQL role
bootstrap. The production compose file, Nginx configuration and runbooks are completed in Phase 15.

## Production settings contract (`portal_config.settings.production`)

The process refuses to start (ImproperlyConfigured) unless:

| Variable | Rule |
|---|---|
| `DJANGO_SECRET_KEY` | ≥ 50 random characters, not `django-insecure…` |
| `DJANGO_ALLOWED_HOSTS` | explicit host names, no `*` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | optional; `https://` origins only |
| `DB_ENGINE` | `postgresql` |
| `DB_USER` / `DB_PASSWORD` | the **runtime** role (`portal_app`); not `postgres`/`root`/`admin`; password required |
| `DB_SSLMODE` | `require` (default), `verify-ca` or `verify-full`; weaker modes only with `DB_ALLOW_INSECURE_TRANSPORT=true` for a private network |
| `REDIS_URL` | required (rate limiting must be shared across workers) |
| `DJANGO_ADMIN_ENABLED` | must be unset/false |
| `TRUSTED_PROXY_COUNT` | number of reverse proxies that append to `X-Forwarded-For` (1 behind Nginx); enables `X-Forwarded-Proto` trust |

`wsgi.py`/`asgi.py` default to production settings, so a server started without `DJANGO_SETTINGS_MODULE`
never runs with DEBUG.

Verify before every release:

```bash
DJANGO_SETTINGS_MODULE=portal_config.settings.production python manage.py check --deploy --fail-level WARNING
```

HSTS preload is **off** by default (`SECURE_HSTS_PRELOAD=false`; Django's W021 is silenced only in that case).
Enable it only when every subdomain is permanently HTTPS — removal from browser preload lists takes months.

## Database roles

`deploy/postgres/init/01-roles.sh` (run once by the PostgreSQL bootstrap superuser) creates:

* `portal_owner` — owns the database and schema; used **only** for `python manage.py migrate`.
* `portal_app` — runtime role: `CONNECT`, schema `USAGE`, DML on tables created by the owner; cannot create
  or alter tables; `statement_timeout=30s`, `idle_in_transaction_session_timeout=5min`.
* `btree_gist` extension (for timetable exclusion constraints).

Migrations: run with `DB_USER=$DB_OWNER_USER DB_PASSWORD=$DB_OWNER_PASSWORD python manage.py migrate`
(the compose `migrate` service does this). The web process always uses `portal_app`.

## Image

`Dockerfile`: multi-stage, `python:3.13-slim`, wheels built in a builder stage, runs as the unprivileged
`portal` user, static files collected at build time with build-only settings (random key, no DB),
Gunicorn on :8000, `/healthz` health check. Private uploads live in `/app/var/private-media` (mount a volume).

## Secret rotation runbook (summary)

1. Generate the new value (`python -c "import secrets; print(secrets.token_urlsafe(64))"`).
2. Update the secret store / environment; for `DJANGO_SECRET_KEY` keep the old key in `DJANGO_SECRET_KEY_FALLBACKS`
   for one session lifetime (wired in Phase 3).
3. Restart the web processes; verify `/healthz` and `check --deploy`.
4. Revoke the old value (DB: `ALTER ROLE portal_app PASSWORD '<new>'` executed **before** step 3).
5. Record the rotation in the operations log (who, when, why — never the value).

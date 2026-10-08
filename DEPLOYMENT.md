# Deployment

Status: **Phase 15 complete.** The deliverables are:

* a production compose stack (`docker-compose.prod.yml`);
* the Nginx and Gunicorn configuration (`deploy/`);
* least-privilege database roles;
* scheduled jobs, backups with an automated restore test (`BACKUP_AND_RESTORE.md`);
* the secret rotation runbook below.

CI validates the compose file, runs `nginx -t` on the rendered Nginx configuration, shellchecks the scripts, and runs `check --deploy` with production settings. The configuration has **not** been exercised on a real production host from this repository. Do a staging deployment and run `scripts/security_probe.py` against it before going live.

## 1. Topology

```
Internet ──443/80──▶ nginx ──(edge)──▶ web (gunicorn, Django)
                                     ├─(data, internal)─▶ db (PostgreSQL 16), redis
            scheduler (run_jobs --group maintenance) ─(data)┤      └─(egress)─▶ SMTP
            backup    (run_jobs --group backup, ops image) ─┘──▶ backups volume ──▶ offsite_copy.sh (host)
```

* **Only Nginx publishes ports.** The `data` network is `internal: true`, so PostgreSQL and Redis have no route to the internet and cannot be reached from it.
* Every application container runs with:
  * a read-only root filesystem;
  * `cap_drop: ALL` and `no-new-privileges`;
  * the unprivileged `portal` user.
* Private uploads live in the `private_media` volume:
  * read-write for `web`;
  * read-only for `nginx` and `backup`.
* The runtime role `portal_app` is used by `web` and `scheduler`.
* The schema owner `portal_owner` is used only by the one-shot `migrate` service and by `pg_dump` in `backup`.

## 2. First deployment

Prerequisites:

* a Linux host with Docker Engine 24+ and Compose v2;
* a DNS name pointing at the host;
* a firewall allowing only 22 (restricted), 80 and 443;
* full-disk or volume encryption for the Docker data directory.

1. **Configuration directory.** Create `/etc/school-portal/`, owned by root with mode 700.
   * Copy each `deploy/env/*.env.example` into it without the `.example` suffix.
   * Replace every `<placeholder>`. Generate each password and key separately.
   * Set each file to mode 600.
2. **TLS certificate.**
   * Put `fullchain.pem` and `privkey.pem` in `/etc/school-portal/tls/` (mode 600).
   * With Let's Encrypt, use the webroot method: Nginx serves `/.well-known/acme-challenge/` from `/var/www/acme`.
   * Renew with `certbot renew --deploy-hook "docker compose -f … exec nginx nginx -s reload"`.
3. **Build and push the images**, tagged with the release version:
   ```bash
   docker build --target runtime -t registry.example.ac.ke/school-portal:2026.10.0 .
   docker build --target ops -t registry.example.ac.ke/school-portal-ops:2026.10.0 .
   ```
4. **Start the stack:**
   ```bash
   docker compose -f docker-compose.prod.yml --env-file /etc/school-portal/compose.env up -d
   ```
   * On first start, PostgreSQL runs `deploy/postgres/init/01-roles.sh` (section 5).
   * `migrate` applies migrations as the owner and syncs the capability catalog.
   * `web` starts once that has succeeded.
5. **Create the first superadmin.** The command prints a one-time password and an MFA enrollment code, to be handed over out of band:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file /etc/school-portal/compose.env \
     run --rm web python manage.py create_portal_superadmin --username <name> --email <address>
   ```
6. **Verify.** Run `check --deploy` (section 9), and run `python scripts/security_probe.py https://<host>` from another machine. Then sign in, enrol MFA, and open **Administration → Operations & backups** the next day: the backup should be on schedule and the restore test should have passed.

## 3. Production settings contract (`portal_config.settings.production`)

The process refuses to start (ImproperlyConfigured) unless every rule below holds.

| Variable | Rule |
|---|---|
| `DJANGO_SECRET_KEY` | ≥ 50 random characters, not `django-insecure…` |
| `DJANGO_SECRET_KEY_FALLBACKS` | optional; previous keys during a rotation only |
| `DJANGO_ALLOWED_HOSTS` | explicit host names, no `*` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | optional; `https://` origins only |
| `MFA_ENCRYPTION_KEYS` | ≥ 1 valid Fernet key (first encrypts, all decrypt) |
| `PORTAL_HMAC_KEY` | ≥ 32 characters, different from the secret key |
| `PORTAL_HMAC_KEY_FALLBACKS` | optional; former HMAC keys (≥ 32 chars) during a rotation |
| `DB_ENGINE` | `postgresql` |
| `DB_USER` / `DB_PASSWORD` | the **runtime** role (`portal_app`); not `postgres`/`root`/`admin`; password required |
| `DB_SSLMODE` | `require` (default), `verify-ca` or `verify-full`. Weaker modes need `DB_ALLOW_INSECURE_TRANSPORT=true`, which the compose stack sets because the database is reachable only over the internal `data` network. |
| `REDIS_URL` | required (rate limiting must be shared across workers) |
| `DJANGO_ADMIN_ENABLED` | must be unset/false |
| `TRUSTED_PROXY_COUNT` | number of reverse proxies that append to `X-Forwarded-For` (`1` behind Nginx). It also enables trust of `X-Forwarded-Proto`. |
| `PRIVATE_FILES_X_ACCEL_PREFIX` | `/_protected/` behind Nginx. Django authorises, then Nginx sends the file. |
| `BACKUP_DIR`, `BACKUP_KEEP`, `BACKUP_DB_*`, `RESTORE_DB_*` | backup service only (see `BACKUP_AND_RESTORE.md`) |

`wsgi.py`/`asgi.py` default to the production settings, so a server started without `DJANGO_SETTINGS_MODULE` never runs with DEBUG.

HSTS preload is **off** by default (`SECURE_HSTS_PRELOAD=false`); Django's W021 is silenced only in that case. Enable preload only when every subdomain is permanently HTTPS, because removal from browser preload lists takes months.

## 4. Nginx and Gunicorn

The Nginx configuration is `deploy/nginx/templates/portal.conf.template`, rendered by the official image. It substitutes only `${PORTAL_HOST}` (`NGINX_ENVSUBST_FILTER=^PORTAL_`).

| Concern | Setting |
|---|---|
| TLS | TLS 1.2/1.3 with the Mozilla intermediate ciphers; session tickets off; HTTP→HTTPS redirect |
| Unknown `Host` | dropped (`return 444` on :80, `ssl_reject_handshake` on :443) |
| Version disclosure | `server_tokens off`; the upstream `Server` header is not passed (closes P14-12) |
| Body and header limits | `client_max_body_size 12m`, equal to `MAX_REQUEST_BYTES` (a test keeps the two in sync); 15 s body/header timeouts |
| Rate limits | 10 req/min per IP on `/accounts/login|mfa|password/reset/`; 20 req/s general; 50 connections per IP. These are coarse; the application's own throttles are the precise control. |
| Client address | `X-Forwarded-For` is **overwritten** with `$remote_addr`, and Django uses `TRUSTED_PROXY_COUNT=1` |
| Request id | `X-Request-ID: $request_id`, so Nginx and application logs correlate |
| Private files | `location /_protected/ { internal; … }`, reachable only through `X-Accel-Redirect` after Django's authorization check. The protective headers (nosniff, sandbox CSP, no-store) are re-added there. |
| Dotfiles and artefacts | `/.git`, `/.env`, `*.sql`, `*.dump` and similar always return 404 |

The Gunicorn configuration is `deploy/gunicorn.conf.py`:

* sync workers, 2×CPU+1 (at most 9);
* 30 s timeout, workers recycled every ~1000 requests;
* header limits;
* heartbeat files in `/dev/shm` (the root filesystem is read-only);
* access log **without query strings**, because searches can contain personal data;
* tunable with `GUNICORN_*` variables.

## 5. Database roles

`deploy/postgres/init/01-roles.sh` is run once by the PostgreSQL bootstrap superuser. It creates three roles:

* **`portal_owner`** owns the database and schema. It is used **only** for `migrate` and by `pg_dump`.
* **`portal_app`** is the runtime role:
  * `CONNECT`, schema `USAGE`, and DML on tables created by the owner;
  * no DDL;
  * UPDATE/DELETE/TRUNCATE revoked on the append-only audit tables;
  * `statement_timeout=30s` and `idle_in_transaction_session_timeout=5min`.
* **`portal_restore`** (when `DB_RESTORE_USER` is set) has `CREATEDB` only. The restore test creates and drops its own scratch database and has no rights on the live one.

The script also installs the `btree_gist` extension, needed by the timetable exclusion constraints.

## 6. Scheduled jobs

The `scheduler` and `backup` containers run `python manage.py run_jobs --group <group>`. Every run is recorded as a `MaintenanceRun` and shown on **Administration → Operations & backups** (capability `manage_backups`, SUPERADMIN). A job that has not succeeded for twice its interval is flagged **overdue**. A failed job is retried after one hour.

| Job | Command | Every | Group |
|---|---|---|---|
| Publish scheduled announcements | `publish_due_announcements` | 1 min | maintenance |
| Send queued e-mail | `send_outbox` | 1 min | maintenance |
| Expire unanswered hostel offers | `expire_hostel_offers` | 5 min | maintenance |
| Seal the audit streams | `seal_audit_log` | 15 min | maintenance |
| Verify the seal chains | `verify_audit_seals` | 1 day | maintenance |
| Close stale requests | `close_stale_requests` | 1 day | maintenance |
| Delete expired sessions | `clearsessions` | 1 day | maintenance |
| Backup | `backup_portal` | 1 day | backup |
| Restore test | `restore_test` | 7 days | backup |

Run a job by hand with `run_jobs --job <name>`. `run_jobs --once` runs every due job and exits non-zero if any failed, so it also suits an external cron.

Run **exactly one scheduler per group**. Job state is read from the database, so restarts neither skip nor repeat work.

On the host, run `deploy/scripts/offsite_copy.sh` from cron after the nightly backup (see `BACKUP_AND_RESTORE.md`).

## 7. Releases and rollback

1. CI must be green: tests on PostgreSQL, lint, bandit, pip-audit, secret scan, deploy checks, compose, and `nginx -t`.
2. Build and push both images with the new version tag, and set it in `compose.env`.
3. Take a backup:
   ```bash
   docker compose … run --rm backup python manage.py run_jobs --job backup
   ```
4. Roll out:
   ```bash
   docker compose … up -d
   ```
   `migrate` runs before `web` starts.
5. Smoke test: `/healthz`, sign in, the Operations page, and `scripts/security_probe.py`.
6. **Rollback.**
   * Without a migration, roll back by re-deploying the previous tag.
   * With a migration, it is safest to restore the pre-release backup (`BACKUP_AND_RESTORE.md` §5). Write migrations to be backward compatible for one release so that, in most cases, the previous image keeps working on the new schema.

## 8. Secret rotation runbook

Rotate on schedule (yearly), when a person with access leaves, and **immediately** after any suspected leak.

For every rotation:

* record who, when and why in the operations log, never the value;
* generate values with `python -c "import secrets; print(secrets.token_urlsafe(64))"`;
* generate Fernet keys with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

| Secret | Procedure | Effect on users |
|---|---|---|
| `DJANGO_SECRET_KEY` | 1. Put the new key in `DJANGO_SECRET_KEY`.<br>2. Move the old key to `DJANGO_SECRET_KEY_FALLBACKS`.<br>3. Restart `web` and `scheduler`.<br>4. After 12 h (the absolute session lifetime), remove the fallback and restart. | None while the fallback is set. Trusted-device cookies issued with the old key stop working once it is removed. |
| `PORTAL_HMAC_KEY` | 1. New key in `PORTAL_HMAC_KEY`, old key in `PORTAL_HMAC_KEY_FALLBACKS`.<br>2. Restart.<br>3. Ask MFA users to regenerate their recovery codes (Security page).<br>4. After the announced period (e.g. 30 days), remove the fallback. | Codes, recovery codes and device cookies keep working while the fallback is set. Login throttle counters restart. Recovery codes issued before the rotation stop working when the fallback is removed. Security-event `identifier_hash` values before and after the rotation no longer correlate, so keep the old key offline if investigators need that. |
| `MFA_ENCRYPTION_KEYS` | 1. Prepend the new key (`new,old`) and restart.<br>2. Run `python manage.py rotate_mfa_encryption`, which re-encrypts every secret with the first key.<br>3. Remove the old key and restart. | None |
| `portal_app` password | 1. `ALTER ROLE portal_app PASSWORD '…'` as `portal_owner` or the bootstrap superuser.<br>2. Update `DB_PASSWORD` in `portal.env` (and `db.env` for documentation).<br>3. Immediately run `docker compose … up -d web scheduler`. | A few seconds of errors between steps 1 and 3; do it in a quiet period |
| `portal_owner` / `portal_restore` passwords | `ALTER ROLE …`, then update `owner.env`, `backup.env` and `db.env`. No restart is needed: they are read when the next job or migration runs. | None |
| Redis password | 1. Update `redis.env` and the password in `REDIS_URL` in `portal.env`.<br>2. Run `up -d redis web scheduler`. | Rate-limit counters reset |
| SMTP credentials | Update `EMAIL_HOST_*` and restart `web` and `scheduler`. Queued mail is retried. | None |
| TLS private key | Issue a new certificate and key (with certbot: `--force-renewal`), then run `nginx -s reload`. Revoke the old certificate if the key leaked. | None |
| Backup encryption key (age) | 1. Generate a new key pair offline and replace the recipients file used by `offsite_copy.sh`.<br>2. Keep the old private key (offline, two-person custody) until every backup encrypted with it has expired. | None |
| Bootstrap superuser (`POSTGRES_PASSWORD`) | Used only for initialisation. Rotate it with `ALTER ROLE postgres PASSWORD …` and keep it in the password safe, not on the host. | None |

**After a suspected compromise:**

1. Rotate every affected secret.
2. End all sessions:
   ```bash
   docker compose … run --rm web python manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete()"
   ```
3. Run `verify_audit_seals`.
4. Review the audit log and security events for the exposure window.
5. Follow the incident response outline in `SECURITY.md`.

## 9. Pre-release checklist

```bash
DJANGO_SETTINGS_MODULE=portal_config.settings.production python manage.py check --deploy --fail-level WARNING
python manage.py makemigrations --check --dry-run
python scripts/security_probe.py https://<staging-host>     # expect all checks to pass behind Nginx
```

* Staging restore test has passed (Operations page) within the last 7 days.
* `pip-audit` is clean, and base images are rebuilt on the newest patch releases (`postgres:16-alpine`, `redis:7-alpine`, `nginx:1.28-alpine`, `python:3.13-slim-bookworm`). Pin them by digest in your registry.
* Monitoring alerts fire on:
  * `/healthz` failing;
  * a job overdue or failed on the Operations page;
  * `verify_audit_seals` failures;
  * repeated 5xx in the Nginx log.

## 10. Temporary test deployment on Vercel (fake data only)

For trying the portal on the internet before the real server exists. Vercel runs Django as a
serverless function (`api/index.py`, settings `portal_config.settings.vercel`), which means this is
**not** the production design:

* uploads are stored in `/tmp` and vanish when an instance is recycled;
* login throttling counters are per function instance, not shared;
* scheduled jobs do not run (no audit sealing, backups, offer expiry or outbox);
* e-mail, including password-reset codes, is written to the function log (Vercel → Logs);
* request bodies are capped at 4.5 MB by Vercel.

Use only seeded fake accounts. Delete the project when testing is finished.

1. **Database.** In the Vercel dashboard: Storage → Create → **Neon** (Postgres), connect it to the
   project. It adds `DATABASE_URL` and `DATABASE_URL_UNPOOLED`; the settings prefer the unpooled one
   and require TLS.
2. **Project.** Add New → Project → import `austinedearan-stack/stine-school-portal`. Framework
   preset: *Other* (`vercel.json` sets the rest). Environment variable:
   `DJANGO_SECRET_KEY` = output of `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
3. **Schema and demo data** (from this machine, once, with the Neon URL in `var/vercel.env`):
   ```bash
   set -a; . var/vercel.env; set +a
   DJANGO_SETTINGS_MODULE=portal_config.settings.vercel python manage.py migrate --noinput
   DJANGO_SETTINGS_MODULE=portal_config.settings.vercel python manage.py create_portal_superadmin --username admin --email admin@example.test
   # or fake demo accounts (written to var/, never committed):
   DJANGO_SETTINGS_MODULE=portal_config.settings.vercel python manage.py seed_demo_data --allow-non-debug > var/vercel-credentials.txt
   ```
   `DJANGO_SECRET_KEY` in `var/vercel.env` must equal the one set in Vercel: the MFA and one-time-code
   keys are derived from it, so a mismatch makes enrollment codes invalid on the live site.
4. **Deploy.** Every push to `main` deploys automatically. Open `https://<project>.vercel.app/healthz`
   (expect `ok`), then run `python scripts/security_probe.py https://<project>.vercel.app`.
5. **Finish.** Settings → Delete Project, and delete the Neon database.

## 11. Full production-like stack on a Windows PC

Needed to run `docker-compose.prod.yml` (Nginx, Gunicorn, PostgreSQL 16, Redis, scheduler, backups)
locally:

* **WSL 2** with an **Ubuntu** distribution (`wsl --install -d Ubuntu`);
* **Docker Desktop for Windows** (WSL 2 backend; includes Docker Compose v2);
* **mkcert** for a locally trusted TLS certificate for `localhost` (Nginx needs a certificate);
* optional: **Node.js LTS** if you want the Vercel CLI (`npm i -g vercel`) instead of Git-based deploys.

Then copy `deploy/env/*.env.example` into a private folder, fill them in, set `PORTAL_HOST=localhost`
and the `PORTAL_*_FILE` / `PORTAL_TLS_DIR` paths in `compose.env`, and follow section 2 from step 3.

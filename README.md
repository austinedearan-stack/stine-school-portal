# University Student Portal

A Django student information and services portal: units and registration, timetables, hostels, clubs,
student requests and transfers, notifications and an administration area. The server, not the browser,
enforces every security rule.

## Features

- **Accounts and security:** login by student/staff ID or email, throttling that fails closed, TOTP MFA for
  privileged roles, recovery codes, session policy middleware, capability-based authorization with audited denials.
- **Academics:** unit catalogue, registration under row locks (window, prerequisites, capacity, credit limits,
  clashes), registrar override, grade entry and amendment.
- **Timetable:** personal and master timetables with venue, lecturer and student-group clash detection.
- **Hostels:** direct booking and ranked applications, offers with deadlines, transfers, check-out.
- **Clubs:** directory, memberships with approval, officers, events.
- **Requests:** categorised requests and transfers with a code-defined state machine and approvals.
- **Notifications:** inbox, role- and scope-targeted announcements, optional email outbox.
- **Administration:** dashboard, user and academic setup, audit log and security event viewers,
  tamper-evident (hash-chained) audit seals.
- **Operations:** scheduled jobs, backups with a weekly automated restore test, key rotation support.
- **Read-only JSON API** under `/api/v1/` (session-authenticated, own data only).

## Quick start (development)

Requirements: Python 3.12+, PostgreSQL 16 and Redis 7 (or Docker), git.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env              # then replace every <placeholder>
```

**With Docker:** `docker compose up --build` creates the database roles, runs migrations and serves the
development server on <http://127.0.0.1:8000>.

**Without Docker:** set `DB_ENGINE=sqlite` in `.env` for a quick look (concurrency tests are skipped on SQLite),
then `python manage.py migrate && python manage.py runserver`.

Demo data: `python manage.py seed_demo_data` (DEBUG only) creates fake `@example.test` accounts and prints their
random passwords once. For a real deployment create the first superadmin with
`python manage.py create_portal_superadmin --username ... --email ...`.

## Common commands

| Task | Command |
|---|---|
| Run tests (SQLite) | `pytest` |
| Run tests (PostgreSQL) | `DB_ENGINE=postgresql DB_USER=… DB_PASSWORD=… pytest` |
| Lint / security | `ruff check .` · `bandit -q -ll -c pyproject.toml -r apps portal_config` · `pip-audit -r requirements/prod.txt` |
| Secret scan | `python scripts/secret_scan.py` |
| Production config check | `DJANGO_SETTINGS_MODULE=portal_config.settings.production python manage.py check --deploy` |
| Scheduled jobs | `python manage.py run_jobs --once` |
| Backup / restore test | `python manage.py backup_portal` · `python manage.py restore_test` |
| Black-box probe | `python scripts/security_probe.py https://<staging-host>` |

## Deployment

- `docker-compose.prod.yml`: Nginx (the only published service) in front of Gunicorn/Django, with PostgreSQL and
  Redis on an internal network, a scheduler and a backup container. Copy the examples in `deploy/env/` and fill them in.
- `vercel.json` and `api/index.py` support a temporary Vercel demo deployment (not for production use).
- Before production: run the PostgreSQL-only tests, deploy to staging, run `scripts/security_probe.py`
  against it and complete a restore rehearsal.

Design, security and operations notes (architecture, database, deployment, backups, testing) are kept in the
maintainer's local `*.md` files and are intentionally not published in this repository.

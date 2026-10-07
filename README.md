# University Student Portal

A Django-based student information and services portal (units, timetable, hostels, clubs,
student requests and transfers, notifications, administration) built to the specification in
the project brief, with the server — not the browser — enforcing every security rule.

## Project status (honest)

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture, requirements, foundation hardening | **Done** — see below |
| 2 | Models & migrations (schema of `DATABASE.md`) | Not started |
| 3 | Authentication & authorization | Not started (inherited login exists but is **not** trusted — see `docs/AUDIT_EXISTING_CODE.md`) |
| 4–11 | Feature modules | Not started (inherited views exist; many render missing templates) |
| 12–15 | Hardening, testing, adversarial testing, deployment | Not started |

**This is not yet a usable portal.** The inherited code was audited and several critical issues
were found; until each feature phase is complete and tested, treat every feature as unfinished.

Phase 1 delivered:

* Design artifacts: `ARCHITECTURE.md` (architecture, directory layout, authentication design,
  capability catalog + authorization matrix, request state machine, STRIDE threat model, phase plan)
  and `DATABASE.md` (ERD, constraints, locking order, append-only audit design).
* Foundation evaluation: `docs/REPOSITORY_EVALUATION.md` (8 open-source projects; none safe to fork).
* Audit of the inherited code: `docs/AUDIT_EXISTING_CODE.md` (41 findings, each scheduled to a phase).
* Hardened foundation: Django 5.2 LTS with pinned dependencies; environment-only secrets with
  fail-fast production settings; trusted-proxy-aware client IP; stock Django admin unmounted;
  public media serving removed; containment of the critical MFA-enrollment bypass; JSON logging
  with secret redaction; standalone 500 page; least-privilege PostgreSQL roles; non-root Docker
  image; secret scanner, ruff, bandit, pip-audit, pre-commit and CI.
* 54 automated tests (settings safety, deploy checks, headers, CSRF, error pages, client-IP
  spoofing, admin/media exposure, MFA bypass regression, log redaction, repo hygiene) passing on
  PostgreSQL 16 and SQLite.

## Quick start (development)

Requirements: Python 3.12+ (3.13 tested), PostgreSQL 16 and Redis 7 (or Docker), git.

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
cp .env.example .env                                      # then replace every <placeholder>
python -c "import secrets; print(secrets.token_urlsafe(64))"   # use for DJANGO_SECRET_KEY etc.
```

**With Docker (recommended):** `docker compose up --build` — creates the `portal_owner` (migrations)
and `portal_app` (runtime) database roles, runs migrations as the owner, and serves the dev server on
<http://127.0.0.1:8000>. Nothing else is published on the host.

**Without Docker:** set `DB_ENGINE=sqlite` in `.env` for a quick look (concurrency tests are skipped on
SQLite), then `python manage.py migrate && python manage.py runserver`.

Seed data and the `create_portal_superadmin` command arrive in Phases 2–3; until then no accounts exist.

## Common commands

| Task | Command |
|---|---|
| Run tests (SQLite) | `pytest` |
| Run tests (PostgreSQL) | `DB_ENGINE=postgresql DB_USER=… DB_PASSWORD=… pytest` |
| Lint / security | `ruff check .` · `bandit -q -ll -c pyproject.toml -r apps portal_config` · `pip-audit -r requirements/prod.txt` |
| Secret scan | `python scripts/secret_scan.py` (also runs in pre-commit and CI) |
| Production config check | `DJANGO_SETTINGS_MODULE=portal_config.settings.production python manage.py check --deploy` |
| Install git hooks | `pip install pre-commit && pre-commit install` |

## Documentation

`ARCHITECTURE.md` · `DATABASE.md` · `SECURITY.md` · `API.md` · `DEPLOYMENT.md` ·
`BACKUP_AND_RESTORE.md` · `TESTING.md` · `SECURITY_TEST_REPORT.md` · `docs/`

## Seed / test accounts

Development seed accounts (Phase 2+) will use obviously fake `@example.test` addresses
(`admin@example.test`, `student001@example.test`, `staff001@example.test`) with random passwords
printed once by the seed command. No real personal data is ever committed.

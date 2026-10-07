# Testing

## Running

```bash
pip install -r requirements/dev.txt
pytest                                   # SQLite in-memory (fast; concurrency tests skipped)
DB_ENGINE=postgresql DB_NAME=school_portal DB_USER=<role with CREATEDB> DB_PASSWORD=<pw> DB_HOST=localhost pytest
pytest --cov --cov-report=term-missing   # coverage
```

The test role needs `CREATEDB` (Django creates `test_<DB_NAME>`). Never point tests at a production server.
CI (`.github/workflows/ci.yml`) runs: secret scan → ruff → bandit (medium+) → pip-audit → migrations-committed
check → pytest on PostgreSQL 16 with coverage → `check --deploy` with production settings.

## Layout

| Location | Contents |
|---|---|
| `tests/settings/` | production fail-fast checks (subprocess imports with controlled env), deploy checks, settings hygiene |
| `tests/security/` | cross-cutting security tests: headers, CSRF, error pages, client IP spoofing, admin/media exposure, MFA bypass regression, log redaction |
| `tests/test_repo_hygiene.py` | secret scan, forbidden patterns (`csrf_exempt`, raw SQL, `mark_safe`, `|safe`) |
| `apps/<app>/tests/` | unit/integration tests per app (Phase 2+) |
| `tests/authz_matrix/`, `tests/races/` | authorization matrix and PostgreSQL concurrency suites (Phases 3, 5, 7) |

Markers: `postgres` (requires PostgreSQL — skipped on SQLite), `slow`.

## Phase 1 results (2026-10-07)

* 54 passed on PostgreSQL 16.15 and 54 passed on SQLite (Python 3.13.16, Django 5.2.18).
* `ruff check .` clean. `bandit -ll` clean (11 low-severity findings remain in inherited code, each annotated
  with its audit ID and fix phase: S-6 `try/except/pass` ×7 → Phase 4, F-4 `random` ticket numbers ×2 → Phase 9).
* `pip-audit -r requirements/prod.txt`: no known vulnerabilities.
* `manage.py check --deploy --fail-level WARNING` with production settings: clean
  (W021 HSTS-preload deliberately silenced while preload is off; see DEPLOYMENT.md).
* Not verified in this environment: building the Docker image (the sandbox could not reach Docker Hub).
  The build's `collectstatic` step and `docker compose config` were verified separately.

## Required categories (spec §35) — coverage plan

| Category | Phase(s) |
|---|---|
| Unit: models, validators, services, permissions | 2, 3, 5–11 |
| Integration: auth, registration, unit registration, hostel booking, requests, transfers | 3, 5, 7, 9 |
| Authorization: cross-student, student→admin, staff→admin-only, admin→superadmin-only | 3 + every feature phase |
| Security: SQLi, XSS, CSRF, IDOR, privilege escalation, path traversal, malicious upload, brute force, session invalidation | 1 (partial), 3, 9, 13 |
| Race conditions: simultaneous bed booking / unit registration (PostgreSQL, threads + barriers) | 5, 7 |

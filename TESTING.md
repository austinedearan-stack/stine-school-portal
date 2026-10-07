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
| `tests/models/` | Phase 2: DB constraints (check / unique / partial unique / exclusion), append-only ORM guard + DB triggers, capability catalog and role ceilings, seed command |
| `tests/factories.py` | plain-function builders for fake test data (shared by every suite) |
| `apps/<app>/tests/` | unit/integration tests per app (feature phases) |
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

## Phase 2 results (2026-10-07)

* 100 tests collected: 98 passed, 2 skipped on SQLite (Python 3.14.5, Django 5.2.18). The 2 skipped tests are
  PostgreSQL-only (`TRUNCATE` refusal on audit tables, timetable exclusion constraint); no PostgreSQL server was
  available in this environment, so they run in CI (PostgreSQL 16) only. **Not yet verified locally on PostgreSQL.**
* Constraint tests violate each rule through the ORM's `create`/`update` (no form validation in the way) and
  require `IntegrityError` from the database itself.
* Append-only tests cover all four layers that exist at this point: ORM (`save`/`delete`/queryset `update`/`delete`/
  `bulk_update` raise), DB triggers (raw `UPDATE`/`DELETE` refused on SQLite and PostgreSQL, `TRUNCATE` on
  PostgreSQL), redaction of secret-like keys in `changes`, keyed hashing of attempted identifiers.
* `ruff check .` clean; `bandit -ll` clean; `makemigrations --check` clean.

## Phase 3 results (2026-10-07)

* 169 passed, 2 skipped (PostgreSQL-only) on SQLite. New suites in `tests/auth/`: login, throttling, MFA,
  session policy + password change/reset, authorization and account-admin invariants, management commands;
  `tests/security/test_mfa_enrollment_bypass.py` updated to the final flow.
* `tests/helpers.py`: `login()` builds a session that passes the session policy (auth time, activity, MFA stamp);
  `enrol()` gives a user a confirmed authenticator; `totp_code()` generates codes for a given time-step.

## Phase 4 results (2026-10-08)

* 209 passed, 2 skipped (PostgreSQL-only) on SQLite. New suites in `tests/profile/`: upload pipeline (22),
  profile editing / photo access / student privacy (13), dashboards (4); template hygiene test for CSP
  compatibility (no inline script, event handlers or style attributes).
* Manual check in a browser against a local development server with seeded fake data: sign-in, student dashboard
  and profile render with the self-hosted stylesheet and no CSP violations in the console.

## Phase 5 results (2026-10-08)

* 243 passed, 5 skipped (PostgreSQL-only) on SQLite. `tests/academics/test_registration.py` (33): every registration
  rule, drop/deadline/minimum credits, override, grading, and view-level IDOR/CSRF/role checks.
* `tests/academics/test_registration_races.py` (3, PostgreSQL only): last seat, credit limit and double submit with
  real threads and a barrier. **Not run in this environment (no PostgreSQL); they run in CI.**
* Hygiene guard: `select_for_update()` combined with `select_related()` must use `of=("self",)` — otherwise
  PostgreSQL also locks joined rows (program, semester) and serialises unrelated registrations. Found and fixed
  during this phase before it shipped.

## Phase 6 results (2026-10-08)

* 258 passed, 5 skipped. `tests/timetable/test_timetable.py` (15): venue / lecturer / group clashes, back-to-back and cross-semester slots,
  capacity, self-overlap on edit, lecturer change, capability checks, personal vs master visibility, POST-only delete.

## Phase 7 results (2026-10-08)

* 279 passed, 7 skipped. `tests/hostels/test_hostels.py` (21): booking, gender policy, maintenance, windows, cancel, application/offer/accept/
  decline/expiry (lazy and command), transfer/vacate, capability checks, UI booking, offer IDOR, special-needs privacy.
* `tests/hostels/test_hostel_races.py` (2, PostgreSQL only): last bed and one-bed-per-student under concurrency.
* Fix found by the tests: refusing an expired offer used to roll back the expiry itself; the expiry now commits and the
  refusal is raised afterwards.

## Phase 8 results (2026-10-08)

* 295 passed, 7 skipped. `tests/clubs/test_clubs.py` (16): approval by default, open clubs, duplicate/rejoin, advisor/officer decisions,
  officers without approval rights, self-action refusal, officer-vs-officer removal, appointment rules and
  self-promotion, advisor reassignment, event visibility and notifications, member-list privacy, id-guessing.

## Phase 9 results (2026-10-08)

* 325 passed, 9 skipped (PostgreSQL-only). `tests/requests/test_requests.py` (30 + 1 PostgreSQL): numbering,
  routing/priority, visibility by role and department, queue scope, the state machine (owner, reviewer, approver,
  invalid jumps, arbitrary POSTed statuses), internal notes, attachment limits/access/malicious PDF, assignment
  scope, transfers (approval vs execution, separation of duties, once-only, snapshot check, auto-close exemption).
* `test_request_numbering_race.py` (PostgreSQL): parallel submissions get distinct sequential numbers.
* Design note: the independent `audit` connection (D16) is switched off in the test suite by default
  (`AUDIT_INDEPENDENT_CONNECTION=False`) and enabled per test, so ordinary tests are not blocked by pytest-django's
  per-database access guard; the PostgreSQL test proves a refused transition's audit row survives the rollback.

## Phase 10 results (2026-10-08)

* 344 passed, 9 skipped. `tests/administration/test_admin_panel.py` (19): per-page capability gating (an admin without capabilities sees
  nothing), account creation without passwords, admin-role creation limited to `manage_roles`, audited record edits
  and notifications, role/group changes through the UI with the ceiling enforced, MFA reset code never emailed,
  IT support vs admin accounts, setup create/edit audit, prerequisite cycle refusal, single current semester,
  audit-log filtering.
* Bug found by the tests: forms used `instance.pk` to detect new objects, but UUID primary keys exist before saving;
  fixed with `_state.adding` (and checked that no other code relies on `pk` for this).

## Required categories (spec §35) — coverage plan

| Category | Phase(s) |
|---|---|
| Unit: models, validators, services, permissions | 2, 3, 5–11 |
| Integration: auth, registration, unit registration, hostel booking, requests, transfers | 3, 5, 7, 9 |
| Authorization: cross-student, student→admin, staff→admin-only, admin→superadmin-only | 3 + every feature phase |
| Security: SQLi, XSS, CSRF, IDOR, privilege escalation, path traversal, malicious upload, brute force, session invalidation | 1 (partial), 3, 9, 13 |
| Race conditions: simultaneous bed booking / unit registration (PostgreSQL, threads + barriers) | 5, 7 |

# University Student Portal

A Django-based student information and services portal (units, timetable, hostels, clubs,
student requests and transfers, notifications, administration) built to the specification in
the project brief, with the server — not the browser — enforcing every security rule.

## Project status (honest)

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture, requirements, foundation hardening | **Done** — see below |
| 2 | Models & migrations (schema of `DATABASE.md`) | **Done** — see below |
| 3 | Authentication & authorization | **Done** — see below |
| 4 | Student dashboard & profile | **Done** — see below |
| 5 | Units & registration | **Done** — see below |
| 6 | Timetable | **Done** — see below |
| 7 | Hostels | **Done** — see below |
| 8 | Clubs & societies | **Done** — see below |
| 9 | Requests & transfers | **Done** — see below |
| 10 | Admin panel | **Done** — see below |
| 11 | Notifications & announcements | **Done** — see below |
| 12 | Security hardening | **Done** — see below |
| 13 | Automated testing | **Done** — see below |
| 14 | Adversarial testing | **Done** — see below |
| 15 | Deployment preparation | Not started |

**All specified modules are implemented and tested (Phases 1–11).** Security hardening, the full test and
adversarial campaigns and deployment preparation (Phases 12–15) are still to come; do not deploy to
production before they are complete.

Phase 14 delivered:

* Attacker-mindset review of every module plus a scripted adversarial campaign (`tests/adversarial/`, 28 tests) and a
  black-box HTTP probe (`scripts/security_probe.py`, 26 checks) against a running server.
* Found and fixed: Unicode look-alike usernames (fullwidth / Cyrillic) could create twin accounts and dodge
  per-identifier throttling — identifiers are now NFKC-normalised everywhere and usernames are ASCII-only.
* Confirmed over HTTP: headers, CSRF, generic failures, throttling, API refusal, no debug detail, sensitive paths.
  Full results and accepted residual risks in `SECURITY_TEST_REPORT.md` (P14-01 … P14-12).

Phase 13 delivered:

* 640 automated tests (plus 9 PostgreSQL-only concurrency/trigger tests that run in CI), 89.8 % branch coverage with a
  CI floor of 88 %. Every spec §35 category is mapped to its suites in `TESTING.md`.
* New suites: authorization matrix (232 role × page cases), SQL injection / stored and reflected XSS / CSRF, end-to-end
  UI flows through the real forms and templates for every module, JSON API, operator commands and approver scopes.
* The read-only JSON API of ARCHITECTURE.md D2 (`/api/v1/me/…`, `/api/v1/offerings/`), session-authenticated with
  CSRF, own data only (`API.md`).
* Removed the last inherited helper module (`apps/core/utils.py`).

Phase 12 delivered:

* Strict CSP with no CDN, no inline script or style and no JavaScript at all (pages are plain HTML forms), plus
  `object-src`/`frame-src`/`base-uri` locked down and `upgrade-insecure-requests`; COOP, CORP, Permissions-Policy,
  `no-store` caching for authenticated pages, `security.txt` and `robots.txt`.
* Request-size cap checked from `Content-Length` before the body is read (413), a coarse per-user/IP cap on
  state-changing requests (fail-open, D17) on top of the fail-closed authentication throttles.
* Request ids on every response, audit row and log line.
* Tamper evidence for the append-only streams: `manage.py seal_audit_log` hash-chains settled rows into
  `AuditSeal`s (copied to the log stream); `manage.py verify_audit_seals` detects edited, deleted, replaced or
  inserted rows and broken chains (exit code 1 for monitoring).
* Automated sweep: every URL in the project refuses anonymous users (except a short public allowlist) and every
  administrative URL refuses students.

Phase 11 delivered:

* Notification inbox: own notifications only; open/mark-read and mark-all-read are POST-only (audit Z-10); links are
  route names resolved from an allowlist at display time (no stored URLs, no open redirect). Notifications are
  produced by every module (registration, timetable changes, hostel offers, club decisions, request progress,
  security changes).
* Announcements targeted by role audience (everyone / students / staff) AND scope (university, faculty, department,
  program, unit offering, club, named individuals); visibility is decided in one selector and enforced on the list,
  detail page, attachments and dashboard (audit Z-1, Z-2). Drafts, future-dated and expired items are hidden.
* Publishing with `publish_announcements`: admins any scope; staff only their own department, their own offerings,
  clubs they advise, or named students registered in their own offerings. Publishing notifies exactly the audience,
  once; future-dated items are delivered by `manage.py publish_due_announcements`. Authors or admin publishers withdraw.
* Optional email copies (`NOTIFICATION_EMAIL_KINDS`) through an outbox sent by `manage.py send_outbox`, with retries.
* All feature modules (Phases 4–11) are now rebuilt on the Phase 2 schema and mounted; no inherited view remains.

Phase 10 delivered:

* Admin dashboard (`view_statistics`): enrolment, registrations, credit hours, open requests, bed occupancy, failed
  sign-ins and permission denials in the last 24 hours, recent audit activity (with `view_audit_logs`).
* Users: search by name/username/email/number, role and status; account detail with record, MFA state, groups and
  effective capabilities. Actions follow the account-administration invariants of Phase 3: activate/deactivate
  (ends sessions), send a password reset code, reset MFA or issue an enrollment code (shown once, never emailed),
  role change (groups stripped) and group assignment limited to groups allowed for the role (`manage_roles`).
* Student and staff accounts are created without a password: the person receives a one-time code and sets their
  own (admins never set passwords). Admin/superadmin accounts only with `manage_roles`. Student and staff records
  are edited through audited services (before/after); department changes notify the staff member.
* Academic setup (`manage_academics` / `manage_units`): faculties, departments, programs, academic years,
  semesters (one current), units with cycle-free prerequisites, offerings with lecturer assignment that updates the
  timetable and re-checks clashes.
* Audit log and security event viewers (`view_audit_logs`) with filters; both are read-only by construction.
* With Phases 6–9 this completes the capability-gated management UIs (timetable, hostels, clubs, request settings).

Phase 9 delivered:

* Students submit requests by category (subject, description, attachments through the secure upload pipeline) and
  transfer requests (program, department, faculty or campus) with a snapshot of their current record.
  Numbers `REQ-YYYY-NNNNNN` come from a locked per-year counter (audit F-4); priority and routing department come
  from the category, never from the student (Z-8).
* Code-defined state machine (ARCHITECTURE.md §5.4): every transition checks who may perform it, takes a row lock,
  writes the append-only history, an audit row and a student notification; arbitrary statuses from a POST are
  refused and the refusal is recorded on the independent connection (Z-4). A student's reply to "needs information"
  moves the request back to review automatically.
* Department-scoped review queue (assigned or department; `review_all_requests` removes the scope), assignment only
  to in-scope reviewers, rerouting, public messages and staff-only internal notes and attachments; nothing can be
  added to closed or cancelled requests (Z-9). Attachments download only through authorization-checked views.
* Approvals by the category's approval capability (staff approvers scoped to their department; never one's own
  request). Approved transfers change nothing until `execute_transfers` applies them: once only, a different person
  from the approver, and only if the student's record still matches the snapshot (Z-5). Approved, unexecuted
  transfers are never auto-closed.
* `manage.py close_stale_requests` closes finished requests after `REQUEST_AUTO_CLOSE_DAYS` (default 14).
* Request settings (`manage_request_config`): categories, routing department, default priority, approval
  requirement and attachment limits; the transfer category's approval rules are fixed.

Phase 8 delivered:

* Directory of clubs and societies with search, kind and category filters; club pages with meeting info, advisor,
  public events (members-only events visible to members, officers, the advisor and the office).
* Membership: requests are PENDING unless a club explicitly opts out of approval (audit Z-7); leave/withdraw;
  re-joining after leaving is allowed. Decisions by the advisor, `manage_clubs` holders, or officers with
  "can approve members" — officers decide on ordinary members only and nobody acts on their own membership.
* Officer appointment (position, approval right) only by the advisor or `manage_clubs`; an advisor cannot reassign
  the advisor role. Leaving drops any office. Member lists are visible only to officers, the advisor and the office.
* Events by officers/advisor/office with member notifications; club creation by `manage_clubs`.

Phase 7 delivered:

* Hostel catalogue with derived availability (free beds per hostel), house rules and free-bed lists.
* Students: direct booking during a DIRECT_BOOKING window; applications with up to three ranked preferences during an
  APPLICATION window; accept/decline offers before their deadline; cancel. Gender policy and one-bed-per-semester enforced.
* Accommodation office (`manage_hostels`): review applications (special needs visible only to the applicant and the
  office), offer beds with an acceptance deadline, direct offers, transfers (both beds locked in id order),
  check-out, room/bed maintenance status, booking windows, and adding hostels and floors of rooms in bulk.
* Expired offers lapse lazily inside every booking/offer transaction and through `manage.py expire_hostel_offers`.
* Locks in the documented order (student, bed(s), allocation/application) with the partial unique indexes as backstop.

Phase 6 delivered:

* Personal timetables (students: registered classes; lecturers: teaching) and a public master timetable filterable
  by department, venue, day and student group.
* Timetable management for `manage_timetable` holders: classes and venues, with venue, lecturer and student-group
  clash detection (readable messages; PostgreSQL exclusion constraints as backstop), venue-capacity check against
  enrolment, locks in the documented order, audit with before/after, and notifications to the lecturer and every
  registered student on each change.
* Changing an offering's lecturer updates its timetable entries in the same transaction and re-checks clashes.

Phase 5 delivered:

* Unit catalogue for the current semester with search, department/level filters and pagination; offering pages show
  prerequisites, schedule, seats and the student's eligibility.
* Registration service enforcing, under row locks (student, then offering) with the partial unique index as backstop:
  registration window, offering status, academic/disciplinary status, program restriction, minimum year,
  prerequisites (passed), capacity, per-semester credit limit, one section per unit, timetable clashes.
  Drop until the add/drop deadline with the minimum-credit rule. Notifications and audit for every change.
* Registrar override (`manage_students`): outside the window only, every other rule still applies, reason mandatory
  and audited.
* Lecturers: "My teaching" and class lists for their own offerings; grade entry with `record_grades` (own offerings,
  first entry only); amendments with `manage_grades`, audited with before/after. Students see results in their history.

Phase 4 delivered:

* Role-aware dashboards built only from the signed-in user's data (student: units, week timetable, hostel,
  open requests; lecturer: teaching and assigned requests; admin: capabilities and, with `view_statistics`, statistics).
* Profile pages: institutional fields read-only; students edit contact/emergency fields and staff edit office/phone
  through explicit allowlists (mass assignment refused); every change audited with before/after.
* Secure upload pipeline (`apps/core/files.py`): content-sniffed types matched to the extension, size limits on the
  real content, image decode + re-encode (EXIF and polyglot payloads removed, pixel bombs refused), PDF active-content
  rejection including compressed streams, random storage names, optional malware-scanner hook, authorized downloads
  with safe `Content-Disposition`, `nosniff` and a sandbox CSP. Profile photos use it.
* Student privacy (audit Z-3): students see only themselves; lecturers see name/ID/program of students in their own
  offerings and no contact details; full records need `manage_students`. Out-of-scope lookups return 404 and are recorded.

Phase 3 delivered:

* Login by student/staff ID or email with uniform failure responses; HMAC-subject throttling with
  per-pair back-off, per-subject slow-down and a device-cookie bucket (attackers cannot lock owners out);
  the limiter fails closed (503).
* TOTP MFA for every admin, superadmin and capability holder; first enrollment requires a one-time code
  delivered out of band; recovery codes; re-authentication before security changes; optional MFA for others.
* Password change, and reset by emailed one-time code (no tokens in URLs); every password change ends other
  sessions, revokes trusted devices and outstanding codes, and notifies the user.
* Session policy middleware: idle/absolute lifetimes (shorter for admins), MFA gate, forced password change.
* `authorize()` with named policies, view decorators and DRF permissions; denials are recorded.
  Account-administration services enforce the role-ceiling and last-superadmin invariants.
* Commands: `create_portal_superadmin`, `issue_mfa_enrollment_code`, `rotate_mfa_encryption`, `sync_capabilities`.

Phase 2 delivered:

* Every model of `DATABASE.md` with fresh initial migrations: check constraints for every status vocabulary and
  numeric range, partial unique indexes for "one live X" rules, `is_superuser = false` enforced by the database.
* Append-only audit tables (`AuditLog`, `SecurityEvent`, `AuditSeal`, `RequestStatusChange`): ORM guard, database
  triggers (SQLite + PostgreSQL), `UPDATE/DELETE/TRUNCATE` revoked from the runtime role, server-assigned `seq`.
* PostgreSQL exclusion constraints preventing overlapping timetable entries per venue, lecturer and student group.
* Capability catalog with role ceilings and default groups (data migration + `manage.py sync_capabilities`);
  `has_capability()` ignores grants outside a role's ceiling. Default request categories seeded.
* MFA secrets encrypted at rest (MultiFernet), one-time codes stored as HMAC digests, TOTP replay blocked by
  time-step counter; `manage.py seed_demo_data` (DEBUG only, fake `@example.test` accounts).
* Interim state: the inherited feature pages (units, timetable, hostels, clubs, requests, notifications, admin)
  are unmounted until their phase rebuilds them on the new schema; only sign-in, profile and the dashboard are live.

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

Demo data: `python manage.py seed_demo_data` (DEBUG only) creates fake accounts and prints their random
passwords once. Privileged demo accounts also get a one-time MFA enrollment code. For real deployments create the
first superadmin with `python manage.py create_portal_superadmin --username ... --email ...`.

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

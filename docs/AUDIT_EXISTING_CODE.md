# Audit of the Pre-existing Codebase

Audit date: 2026-10-07 · Scope: every file in the repository as found at the start of Phase 1
(`apps/`, `portal_config/`, `templates/`, Docker files, docs). The `.venv/` directory and `.env`
were **not** read or copied (`.env` may contain secrets and is git-ignored).

The existing code is a reasonable *skeleton* (Django, custom user model, UUID primary keys,
service functions, some transactions and partial unique constraints). It is **not** a working
application and contains several authentication/authorization defects. Nothing below is relied on
until it has been fixed and covered by a regression test.

Severity: **C** critical · **H** high · **M** medium · **L** low. "Phase" = phase in which the fix
lands (see `ARCHITECTURE.md` §9). ✅ = fixed in Phase 1 with a regression test.

## 1. Structural / completeness problems

| ID | Finding | Phase |
|----|---------|-------|
| S-1 | No migrations exist for any app. | ✅ 2 |
| S-2 | No automated tests exist (`pytest.ini` only). | every phase |
| S-3 | ~20 views render templates that do not exist (`requests/*`, `administration/*`, `notifications/*`, `clubs/club_detail.html`, …) → HTTP 500. | 4–11 |
| S-4 | `apps/requests` is placed on `sys.path` via `sys.path.insert(0, BASE_DIR/'apps')`, so `import requests` anywhere in the process resolves to the portal app instead of the PyPI `requests` library (breaks third-party code; confusing import semantics). | ✅ 1 (app renamed `student_requests`, hack removed) |
| S-5 | Pinned to `Django>=5.0,<5.2`; Django 5.0 and 5.1 are end-of-life. `gunicorn` is used by the Dockerfile but is not in `requirements.txt`. Dependencies unpinned. | ✅ 1 (Django 5.2 LTS, pinned) |
| S-6 | Many views swallow every exception (`except Exception: pass`), hiding real defects. | ✅ 7 (last inherited views replaced; no `except: pass` remains) |
| S-7 | Role checks are string comparisons scattered across views (`request.user.role != 'STUDENT'`) rather than the central policy layer the spec requires. | ✅ 3 |
| S-8 | **Admins can never finish logging in.** `mfa_verify_view` returns the tuple from `complete_user_login()` instead of an HTTP response, so the correct TOTP code crashes the request (`TypeError`) after the session is already logged in (found by runtime probe). | ✅ 3 |
| S-9 | First-time MFA enrollment calls `verify_totp()` on an unsaved throw-away `User`, which **inserts a blank, active STUDENT account** (username `''`) on every enrollment; the second admin to enroll then hits a unique-constraint error (found by runtime probe). | ✅ 3 |

## 2. Authentication

| ID | Sev | Finding | Phase |
|----|-----|---------|-------|
| A-1 | **C** | **MFA bypass.** After a correct password, an MFA-enrolled admin is put in a "pre-MFA" session and redirected to `/accounts/mfa/verify/`. The attacker can instead browse to `/accounts/mfa/setup/`, which accepts the same pre-MFA session, enrolls a **new** TOTP secret and completes login — bypassing the victim's second factor with only the password. | ✅ 3 (Phase 1 containment + one-time enrollment codes) |
| A-2 | **C** | **Django admin bypasses MFA.** `/django-admin/` uses Django's stock login (password only). Any `is_staff` account reaches it without MFA. | ✅ 1 (admin site disabled unless explicitly enabled; OTP-protected version in 3) |
| A-3 | H | Hard-coded fallback `SECRET_KEY` in `settings/base.py`; production silently runs with it if the env var is missing. `.env.example` ships `DEBUG=True`. | ✅ 1 |
| A-4 | H | Password reset is a placeholder — no token is generated or sent. | ✅ 3 |
| A-5 | H | Account lockout is per-account only (5 failures → 15 min). Anyone can lock any account indefinitely by repeating 5 bad guesses every 15 min (spec §22 forbids this). | ✅ 3 |
| A-6 | M | Account enumeration: unknown usernames never return "account locked", existing ones do; unknown usernames also skip password hashing (timing oracle). | ✅ 3 |
| A-7 | M | TOTP replay protection compares only the *last code string*; with `valid_window=1` a different still-valid code from the previous step is accepted after a newer one. Must track the last accepted time-step counter. | ✅ 2 (step counter + conditional update) |
| A-8 | M | MFA verification is not rate-limited per user/pre-auth session (only a per-IP path rule that is spoofable, see I-1). | ✅ 3 |
| A-9 | M | TOTP secrets stored in plaintext. | ✅ 2 (MultiFernet at rest) |
| A-10 | M | Logout accepts GET (cross-site logout); MFA enrollment allowed without re-authentication from an existing session. | ✅ 3 |
| A-11 | L | Session timeout identical for admins and students; no absolute session lifetime. | ✅ 3 |

## 3. Authorization / IDOR

| ID | Sev | Finding | Phase |
|----|-----|---------|-------|
| Z-1 | H | `announcement_detail_view` returns any published announcement by UUID with no audience check (department/staff-only announcements readable by any user). | 11 |
| Z-2 | H | Student announcement filter uses `Q(department=…)` without `audience`, so **staff-only** announcements scoped to a department leak to that department's students. | 11 |
| Z-3 | H | `can_view_student` lets *every* staff member view *every* student, including phone and emergency contacts (violates least privilege / spec §26). | ✅ 4 |
| Z-4 | H | Admin request panel: new status is taken verbatim from POST (`ticket.status = new_status`) — no allowlist, no state machine; `assigned_to` accepts any staff id. | 9–10 |
| Z-5 | H | Approved transfers can be "executed" repeatedly, outside a transaction, regardless of transfer type. | 9 |
| Z-6 | M | All ADMINs have all admin powers (`role_required(ADMIN, SUPERADMIN)`), no granular permissions (spec §34). | ✅ 3 |
| Z-7 | M | Club join auto-approves membership (spec requires approval). | 8 |
| Z-8 | M | Request `priority` accepted from student POST without validation (mass assignment / 500 on long values). | 9 |
| Z-9 | M | Students can reply to CLOSED/CANCELLED requests; no attachment count limit. | 9 |
| Z-10 | L | Notification "mark read" is a state change via GET. | 11 |

## 4. Input handling / files

| ID | Sev | Finding | Phase |
|----|-----|---------|-------|
| F-1 | H | In DEBUG, all of `MEDIA_ROOT` (request attachments, profile photos) is served publicly by `static()` with **no authorization**; no protected-download design exists for production. | ✅ 1 (public media serving removed); protected downloads 9 |
| F-2 | M | `Content-Disposition` built by string-formatting the user-supplied original filename (header injection / broken header). | ✅ 4 (pipeline in `apps/core/files.py`; request attachments use it in 9) |
| F-3 | M | Image uploads are not decoded/re-encoded (polyglot files survive); PDFs not inspected for active content. | ✅ 4 (pipeline in `apps/core/files.py`; request attachments use it in 9) |
| F-4 | L | Transfer ticket numbers are `TRF-<year>-<4 random digits>` → collisions raise IntegrityError (500) after a few thousand requests; predictable identifiers used in URLs. | 9 |

## 5. Infrastructure / configuration

| ID | Sev | Finding | Phase |
|----|-----|---------|-------|
| I-1 | H | `get_client_ip` trusts the client-controlled `X-Forwarded-For` header unconditionally → rate limits bypassable and audit IPs forgeable. | ✅ 1 |
| I-2 | H | `docker-compose.yml` makes the application's DB user the PostgreSQL **superuser** (`POSTGRES_USER`), ships a default password (`secure_postgres_pass`), and publishes Postgres (5432) and Redis (6379, no password) on all host interfaces. | ✅ 1 |
| I-3 | M | Dockerfile runs as root, uses Python 3.14 image while dependencies were untested there; dev server (`runserver`) used in compose. | ✅ 1 |
| I-4 | M | CSP allows `'unsafe-inline'` scripts and loads Tailwind's *runtime* compiler and Alpine from a CDN (no SRI). | 12 (self-hosted, compiled CSS, Alpine CSP build, nonces) |
| I-5 | M | `AuditLog` immutability enforced only in `Model.save/delete`; `QuerySet.update()/delete()` and raw SQL bypass it. | ✅ 2 (ORM guard + DB trigger + DB privileges) |
| I-6 | L | `CSRF_COOKIE_HTTPONLY=False` without need; `SECURE_BROWSER_XSS_FILTER` (obsolete header). | ✅ 1 |
| I-7 | L | Production DB `sslmode=prefer` (silently falls back to plaintext). | ✅ 1 (`require` by default, configurable) |

## 6. Runtime smoke probe (2026-10-07, PostgreSQL 16)

Every inherited route was requested as anonymous, student, staff and admin with fake data
(throw-away script, not committed). Result:

| Area | What happens today |
|---|---|
| Login / logout / password-change pages | Student and staff login and logout work. Admin login crashes at the MFA step (S-8). Password reset only shows a message (A-4). |
| Student dashboard, profile, profile edit | Render (200) |
| Units: catalogue, detail, my units, register | Render; registration persists a row |
| Timetable: student, master | Render |
| Hostels: catalogue, rooms, my hostel, book | Render; booking persists an allocation |
| Clubs | Directory renders; join persists but auto-approves (Z-7); club detail and "my clubs" crash (S-3) |
| Requests & transfers | Create via POST persists rows; every request page crashes — list, create form, detail, transfer form (S-3) |
| Notifications & announcements | All pages crash (S-3); no notification is ever created by student actions |
| Staff dashboard | Crashes (S-3) |
| Admin panel (dashboard, requests, transfers, users, audit logs) | Unreachable (S-8); with a forced login every page crashes (S-3) |
| Authorization spot checks | Student → admin URLs: 403; anonymous → protected pages: redirect to login |

## 7. Kept from the existing code (after review)

* Custom `User` model with UUID PKs and an explicit `role` field (extended in Phase 2/3).
* App decomposition (core, accounts, academics, timetable, hostels, clubs, requests, notifications, administration).
* Service-function pattern with `transaction.atomic()` + `select_for_update()` for registration and bed booking (logic to be re-verified against PostgreSQL in race tests).
* Partial unique constraints (`unique_active_bed_allocation`, `unique_active_registration…`).
* File-signature checking idea in `core/utils.py` (to be hardened).
* Separation of student-editable vs institutional profile fields in `StudentProfileEditForm`.
* Generic error templates (400/403/404/429/500).

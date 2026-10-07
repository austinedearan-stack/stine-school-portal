# Architecture

Status: **Phase 1 (architecture & requirements) — approved design baseline.**
Companion documents: `DATABASE.md` (schema/ERD), `SECURITY.md` (controls), `docs/AUDIT_EXISTING_CODE.md`
(findings in the inherited code), `docs/REPOSITORY_EVALUATION.md` (foundation decision).

Guiding rule: **the browser is untrusted and the user may be hostile.** Every protected operation is
authorized on the server, validated on the server and, where state can race, protected by the database.

---

## 1. Architecture proposal

### 1.1 Style

A **modular monolith** on **Django 5.2 LTS** (supported until April 2028), served by Gunicorn behind
an Nginx reverse proxy, backed by **PostgreSQL 16** and **Redis 7**.

```
Browser (HTML + HTMX + Alpine CSP build, compiled Tailwind CSS — no CDN, no inline script)
   │  HTTPS only, HSTS, Secure/HttpOnly/SameSite cookies, CSRF token on every unsafe request
   ▼
Nginx ── TLS termination, request size limits, proxies to Gunicorn; serves private files ONLY via
   │      X-Accel-Redirect issued by an authorized Django view (internal location).
   │      Static assets are served by WhiteNoise (hashed, compressed, immutable cache headers).
   ▼
Gunicorn → Django
   ├─ Middleware: SecurityMiddleware → Sessions → CSRF → Auth → SessionPolicy (idle/absolute
   │   timeouts, MFA-required gate for privileged roles) → SecurityHeaders (CSP nonce) → RequestContext
   ├─ Views (thin): parse/validate input with forms/serializers, call a service or selector
   ├─ Selectors: read queries ALREADY scoped to the actor ("what may this user see?")
   ├─ Services: state changes = authorize → validate business rules → transaction/locks → audit → notify
   ├─ Authz layer (apps/core/authz): capability catalog + role ceilings + object policies
   └─ Core: audit log, security events, rate limiter, file validation/storage, structured logging
   │
   ├── PostgreSQL: constraints (FK, UNIQUE, partial UNIQUE, CHECK), row locks, append-only audit
   │   tables enforced by trigger + privileges; app connects as a non-owner, non-superuser role
   └── Redis: rate-limit counters, cache. (Sessions live in PostgreSQL so logout/rotation is durable.)
```

### 1.2 Key decisions

| # | Decision | Reason |
|---|----------|--------|
| D1 | Server-rendered Django templates + HTMX + Alpine.js (CSP build) + Tailwind compiled at build time | No SPA needed; keeps auth in secure server sessions; strict CSP without `unsafe-inline`/`unsafe-eval`. |
| D2 | DRF used only for a small read-mostly `/api/v1/` (profile, my units, timetable, notifications) with **session auth + CSRF**, no tokens | Spec allows APIs "where useful"; avoids token storage/URL tokens. All API views call the same services/selectors. |
| D3 | Single `role` per user (STUDENT, STAFF, ADMIN, SUPERADMIN) **plus** granular *capabilities* (Django permissions granted via groups) bounded by a per-capability **role ceiling** | Least privilege (spec §34) and "admin cannot do superadmin-only things" even if misconfigured. |
| D4 | `is_superuser` is never used for portal accounts (DB CHECK constraint `is_superuser = false`) | Django's superuser short-circuits every `has_perm` check; SUPERADMIN gets explicit capabilities instead and still passes through policies and audit. |
| D5 | Stock Django admin site **never mounted outside DEBUG development** (settings refuse `DJANGO_ADMIN_ENABLED` when `DEBUG=False`) | It bypassed MFA in the inherited code (A-2) and its edits skip services, policies and audit. All administration happens in the portal's own audited admin UI. |
| D6 | TOTP MFA implemented with `pyotp`; secrets encrypted at rest (Fernet/MultiFernet key from env); last accepted time-step stored to block replay; hashed single-use recovery codes | Small, testable, no plaintext secrets; fixes A-1/A-7/A-9. |
| D7 | Rate limiting and lockout implemented in-project on Redis with keys per IP, per (account, IP) and per account, using back-off rather than indefinite lock | Prevents brute force and credential stuffing without letting an attacker lock a victim out indefinitely (spec §22). |
| D8 | Request statuses are a **fixed vocabulary with a code-defined state machine** (§5.4); categories, routing department, priority defaults, assignment and *whether approval is required (and by which approval capability — `approve_requests` or `approve_transfers` only)* are **DB-configurable** by holders of `manage_request_config` | Spec §40 asks for configurable statuses; a DB-editable state machine could let a misconfiguration grant students approval transitions. **Deliberate, documented deviation:** status *labels/descriptions* are configurable, transitions are not. |
| D9 | Transfers are a specialised request (`TransferRequest` 1:1 with `StudentRequest`) so they share messages, attachments, history and notifications; executing an approved transfer is a separate, idempotent, capability-gated operation | Spec §12: approval must not change the academic record automatically. |
| D10 | Clubs and societies share one model `Club` with `kind ∈ {CLUB, SOCIETY}` | Identical behaviour; avoids duplicated tables/code. |
| D11 | Course delivery modelled as `Unit` (catalogue) + `UnitOffering` (unit in a semester with lecturer, capacity, eligibility) | Lecturer, capacity and timetable vary per semester; registration and timetable reference the offering. |
| D12 | Bed occupancy is **derived** from allocations (partial unique index on active allocations), not stored as a boolean on `Bed` | Removes duplicated state that can drift; the database guarantees one holder per bed. |
| D13 | Private files stored outside any web-served directory under random names; downloaded only through authorization-checked views | Fixes F-1/F-2; enables per-object access control. |
| D14 | Two PostgreSQL roles: `portal_owner` (owns schema, runs migrations) and `portal_app` (runtime DML only; INSERT/SELECT only on audit tables) | Spec §27; even a compromised app cannot alter the audit trail or schema. |
| D15 | Password reset uses an **emailed one-time code typed into a POST form** (no token in any URL): 10-character base32 code (50 bits), stored as HMAC-SHA256, valid 20 min, single use, 5 attempts per reset, all reset codes invalidated by any password change | Spec §4 "never put authentication tokens in URLs". |
| D16 | Security-relevant writes that must survive a rollback (permission denials, failed logins, rate-limit hits) go through a second DB alias `audit` (same database, independent autocommit connection); successful business changes write their audit row inside the business transaction | A denial must be recorded even though the denied transaction rolls back; a successful change must never commit without its audit row. |
| D17 | Rate limiter **fails closed** for login, MFA and password reset (Redis unavailable → 503 "try again shortly"), fails open (logged) for low-risk endpoints | Outage must not silently disable brute-force protection. |

### 1.3 Layer rules (enforced by review and tests)

1. Views never call `Model.objects` for protected data directly; they use a **selector** that takes the actor.
2. Every state-changing operation is a **service** function `service(actor, …, *, ctx)` that calls
   `authorize(actor, "<policy>", obj)` first, runs inside `transaction.atomic()`, writes an audit event
   in the same transaction, and returns domain objects. Services never trust IDs from the client without
   re-fetching them through a scoped queryset.
3. Forms/serializers declare **explicit field allowlists** (no `fields="__all__"`, no `exclude`) to prevent mass assignment.
4. Identity always comes from `request.user`; no endpoint accepts a `student_id` to mean "me".
5. Object identifiers in URLs are UUIDs (or human request numbers that are re-checked by policy); 404 is returned
   for objects outside the actor's scope (prevents existence probing), 403 for in-scope objects where the action is not allowed.

---

## 2. Directory structure (target)

```
school-portal/
├── apps/
│   ├── core/                 # cross-cutting, no domain logic
│   │   ├── authz/            # capabilities.py (catalog+ceilings), policies.py (can_* fns), decorators.py, drf.py
│   │   ├── audit.py          # record_audit_event(); AuditLog/SecurityEvent writers
│   │   ├── ratelimit.py      # Redis/cache sliding-window + back-off limiter
│   │   ├── files.py          # upload validation, safe storage, protected download response
│   │   ├── net.py            # trusted-proxy aware client IP
│   │   ├── logging.py        # JSON formatter + secret-redaction filter
│   │   ├── middleware.py     # security headers/CSP nonce, session policy, request context
│   │   ├── models.py         # TimeStampedModel, AuditLog, SecurityEvent, StoredFile, PortalCapability
│   │   └── views.py          # error handlers, health check
│   ├── accounts/             # User, StudentProfile, StaffProfile, MFA, login/logout/reset, settings
│   ├── academics/            # Faculty, Department, Program, AcademicYear, Semester, Unit, UnitOffering,
│   │                         # UnitPrerequisite, UnitRegistration (+ grades)
│   ├── timetable/            # Venue, TimetableEntry, conflict validation
│   ├── hostels/              # Hostel, HostelBuilding, HostelFloor, Room, Bed, BookingWindow,
│   │                         # HostelApplication, HostelAllocation
│   ├── clubs/                # Club (club|society), ClubMembership, ClubEvent
│   ├── student_requests/     # RequestCategory, StudentRequest, RequestMessage, RequestAttachment,
│   │                         # RequestStatusChange, TransferRequest
│   ├── notifications/        # Notification, Announcement, AnnouncementAttachment
│   ├── administration/       # admin portal views (dashboard, user mgmt, audit viewer) — no models
│   └── api/                  # DRF v1 views/serializers calling selectors/services
│   (each domain app: models.py, selectors.py, services.py, forms.py, views.py, urls.py, tests/)
├── portal_config/
│   ├── settings/ base.py development.py testing.py production.py env.py
│   ├── urls.py wsgi.py asgi.py
├── templates/                # base layouts, per-app templates, errors/
├── static/                   # src/tailwind.css, vendor/ (htmx, alpine-csp – pinned, hashed)
├── tests/                    # cross-cutting suites: security/, authz_matrix/, races/, settings/
├── deploy/
│   ├── postgres/init/        # creates portal_owner/portal_app roles, privileges
│   └── nginx/portal.conf
├── scripts/                  # secret_scan.sh, backup.sh, restore.sh, restore_test.sh
├── requirements/             # base.txt, dev.txt, prod.txt (pinned)
├── docs/                     # audit, repo evaluation, ADRs
├── Dockerfile  docker-compose.yml  .env.example  pyproject.toml  pytest.ini
├── .pre-commit-config.yaml  .github/workflows/ci.yml  Makefile
└── README.md ARCHITECTURE.md DATABASE.md SECURITY.md API.md DEPLOYMENT.md
    BACKUP_AND_RESTORE.md TESTING.md SECURITY_TEST_REPORT.md
```

---

## 3. Database (summary)

Full ERD, constraints and indexes are in `DATABASE.md`. Principles: UUID primary keys; every FK
explicit with `PROTECT` for institutional data (no cascading loss of academic history); partial
unique indexes for "one active X"; CHECK constraints for ranges/time ordering; `created_at/updated_at`
everywhere; soft deletion via `is_active`/status for users, units, clubs, hostels (never hard-delete
records that audit or history point to); append-only audit tables.

---

## 4. Authentication architecture

### 4.1 Login

```
GET /accounts/login/ ─► form (CSRF token)
POST identifier+password
  1. Normalise the identifier (trim, lower-case) and derive the throttle subject = HMAC(normalised identifier).
     The subject never depends on whether the account exists or on links between ID and email, so throttling
     cannot be used as an existence or linking oracle (cost: a user's ID and email have separate budgets).
  2. Throttle check (counts failures only):
       ip               : 100 failures / 15 min → 429 for that IP, except requests presenting a valid
                          device cookie (so a shared campus NAT cannot be used to lock out known devices)
       subject+ip       : 5 failures → back-off 1,2,4…≤30 min for that pair only
       subject (all ips): 30 failures / h → "untrusted-device slow-down": 1 attempt / 60 s for that
                          subject from devices WITHOUT a valid device cookie; alert to security log.
       Device cookies (OWASP "device cookie" pattern): after a successful login the browser receives a
       signed, HttpOnly cookie binding (user, random nonce). Attempts that present a valid device cookie for
       the subject are counted in their own bucket (5 failures → back-off for that cookie only), so an attacker
       hammering an account cannot lock the owner out of their known devices; a new-device owner waits at
       most 60 s per attempt (never an indefinite lock).
     Unknown identifiers use the same rules → identical behaviour (no enumeration via throttling).
     Limiter unavailable → fail closed (D17).
  3. Resolve identifier (case-insensitive username = student/staff ID, or email).
  3. authenticate(): Argon2id verify; for unknown users a dummy hash is computed (constant-ish time).
     Inactive users fail with the same generic error.
  4. Failure → generic "Invalid credentials" (same text, status and template for every cause),
     SecurityEvent LOGIN_FAILURE (identifier hashed, never the password).
  5. Success, and the user is *MFA-required* (role ∈ {ADMIN, SUPERADMIN}, or holds any capability —
     i.e. every privileged STAFF account) or has MFA enrolled:
       session.cycle_key(); session["preauth"] = {user_id, auth_hash, started_at, attempts:0}
       – no user is logged in yet –
       if confirmed MFA device exists → /accounts/mfa/verify/
       elif MFA required              → /accounts/mfa/enroll/ (first enrollment ONLY, and only with a
                                          valid one-time enrollment code — see §4.2)
  6. Success otherwise → login() (rotates session key), session["auth_time"]=now, audit AUTH_LOGIN.
  Redirect target: fixed dashboard, or `next` only if url_has_allowed_host_and_scheme() and relative.
```

### 4.2 MFA

* TOTP (RFC 6238, 30 s, 6 digits, ±1 step), secret encrypted with `MFA_ENCRYPTION_KEYS` (MultiFernet → rotation).
* Replay: store `last_used_step`; accept a code only if its step > `last_used_step` (fixes A-7).
* Throttle: 5 failures per pre-auth session → pre-auth discarded, password required again; 10 / 15 min per user.
* **Enrollment rules (fix for A-1):** a pre-auth session may enroll only if the user has **no confirmed device**
  **and** presents a valid **one-time enrollment code** (16 chars, HMAC-stored, 72 h expiry, single use). The code
  is issued out of band when a privileged account is created, when a capability first makes MFA required, or after
  an MFA reset — so knowing the password alone never lets an attacker enroll their own device. Re-enrollment from a
  logged-in session requires password re-entry + a current TOTP/recovery code. Any device change is audited and
  notified to the user.
* Recovery: 10 single-use recovery codes of 16 base32 chars (80 bits), shown once, stored as HMAC-SHA256 with a
  server pepper (one indexed lookup — no N×Argon2 DoS) and consumed atomically with
  `UPDATE … SET used_at=now() WHERE user_id=<pre-auth user> AND code_hash=… AND used_at IS NULL` (concurrent reuse impossible). If all are lost:
  an MFA reset by a holder of `manage_user_accounts` (for STUDENT/STAFF without capabilities) or `manage_roles`
  (for any account that is MFA-required) after out-of-band identity verification; it terminates all of the user's
  sessions and issues a new enrollment code delivered out of band. No "email me a bypass link" path.
* Privileged sessions: `SessionPolicyMiddleware` rejects any request from an MFA-required user's session
  lacking `session["mfa_verified_at"]` (defence in depth if a view forgets a check).

### 4.3 Sessions & cookies

DB-backed sessions; cookie `HttpOnly`, `SameSite=Lax`, `Secure` (prod), `__Host-` prefix in production.
Idle timeout 30 min (students/staff), 15 min (admins); absolute lifetime 12 h / 4 h. Session key rotated at
login and at MFA completion. Logout = POST + `session.flush()`. Password change → `update_session_auth_hash`
for the current session; all other sessions invalidated by the auth-hash change. CSRF cookie `HttpOnly`;
HTMX sends the token from a `<meta>` tag via `hx-headers`.

### 4.4 Passwords

Argon2id primary hasher (PBKDF2 kept only to upgrade legacy hashes on login). Validators: length ≥ 12,
common-password list, user-attribute similarity, numeric-only, max length 128 (DoS guard). Composition
rules from the inherited code are kept only as a soft requirement — NIST SP 800-63B discourages them; documented.
Reset: emailed one-time code entered on a POST form (D15); identical response and timing whether or not the
account exists; throttled per IP and per subject; email contains only the code and expiry. Administrators
never set another user's password: an admin-initiated reset only sends the user a reset code and forces
`must_change_password`; the user is notified of every password change.

---

## 5. Authorization

### 5.1 Model

`authorize(actor, action, obj=None)` → returns or raises `PermissionDenied` (and records a
`PERMISSION_DENIED` security event). An action passes only if **all** hold:

1. actor is authenticated and active (and the session is MFA-verified if the actor is MFA-required, §4.1 step 5);
2. if the action needs a capability: actor's role is within that capability's **ceiling** **and** actor holds it;
3. the object policy (if any) returns true (ownership, assignment, department scope, state).

Named policy functions (single module `apps/core/authz/policies.py`):
`can_view_student`, `can_edit_student_profile`, `can_manage_students`, `can_view_grades`,
`can_manage_units`, `can_register_units`, `can_manage_timetable`, `can_manage_hostels`, `can_book_bed`,
`can_view_request`, `can_reply_request`, `can_transition_request`, `can_approve_request`,
`can_approve_transfers`, `can_execute_transfer`, `can_manage_club`, `can_review_membership`,
`can_publish_announcement`, `can_view_announcement`, `can_download_file`, `can_manage_users`,
`can_manage_roles`, `can_view_audit_logs`, `can_manage_system`, `can_manage_staff`, `can_manage_academics`,
`can_record_grades`, `can_manage_grades`, `can_manage_request_config`, `can_manage_backups`, `can_view_statistics`,
`can_override_registration`.

### 5.2 Capability catalog & role ceilings

| Capability | Meaning | Ceiling (roles that *may* hold it) | Default group(s) |
|---|---|---|---|
| `manage_students` | create/edit student records & institutional fields | ADMIN, SUPERADMIN | Registrar |
| `manage_staff` | create/edit staff profiles | ADMIN, SUPERADMIN | HR / Registrar |
| `manage_academics` | faculties, departments, programs, years, semesters | ADMIN, SUPERADMIN | Academic Office |
| `manage_units` | units, offerings, prerequisites, lecturer assignment | ADMIN, SUPERADMIN | Academic Office |
| `record_grades` | enter grades **only for offerings the lecturer teaches** (object-scoped) | STAFF | Lecturers |
| `manage_grades` | record/amend any grade (audited with before/after) | ADMIN, SUPERADMIN | Examinations |
| `manage_timetable` | create/edit timetable entries | STAFF, ADMIN, SUPERADMIN | Timetabling |
| `manage_hostels` | hostels, rooms, beds, windows, allocations | ADMIN, SUPERADMIN | Accommodation Office |
| `manage_request_config` | request categories, routing departments, priorities, approval requirement | ADMIN, SUPERADMIN | Student Services |
| `review_requests` | triage, assign, respond, request info (scoped to department unless `review_all_requests`) | STAFF, ADMIN, SUPERADMIN | Student Services, department staff |
| `review_all_requests` | remove department scope | ADMIN, SUPERADMIN | Student Services |
| `approve_requests` | approve/reject approval-type categories | STAFF, ADMIN, SUPERADMIN | Heads of department |
| `approve_transfers` | approve/reject transfer requests | ADMIN, SUPERADMIN | Academic Board |
| `execute_transfers` | apply an approved transfer to the academic record | ADMIN, SUPERADMIN | Registrar |
| `manage_clubs` | create/edit clubs, appoint club leaders, all memberships | ADMIN, SUPERADMIN | Student Affairs |
| `publish_announcements` | publish announcements (staff limited to own department/units) | STAFF, ADMIN, SUPERADMIN | Communications |
| `manage_user_accounts` | activate/deactivate, reset password/MFA for STUDENT/STAFF accounts | ADMIN, SUPERADMIN | IT Support |
| `view_audit_logs` | read audit log & security events | ADMIN, SUPERADMIN | Auditor |
| `view_statistics` | admin dashboard statistics | ADMIN, SUPERADMIN | all admin groups |
| `manage_roles` | change roles, groups, capabilities; manage ADMIN/SUPERADMIN accounts | **SUPERADMIN** | Superadmin |
| `manage_system_settings` | security/system configuration | **SUPERADMIN** | Superadmin |
| `manage_backups` | trigger/verify backups, view restore reports (never download backup files through the UI) | **SUPERADMIN** | Superadmin |

SUPERADMIN accounts are created with the `create_portal_superadmin` management command (never `createsuperuser`, which would set `is_superuser`).

Additional invariants:

* Nobody can change their **own** role, groups, capabilities, club position or membership decision.
* Any role change strips all groups and direct capabilities (prevents dormant grants activating on promotion).
* Granting a capability outside the target's role ceiling is rejected at grant time **and** ignored at check time.
* Nobody approves a request they submitted or a transfer of their own record; executing a transfer requires a
  different person than the approver when `TRANSFER_SEPARATION_OF_DUTIES=True` (default).
* At least one active SUPERADMIN holding `manage_roles` must always remain: every role/group/capability/activation
  change first locks **all** active SUPERADMIN rows (`SELECT … FOR UPDATE`, ordered by id) and re-checks the
  invariant inside the same transaction, so two superadmins cannot demote each other concurrently.
* Changing a staff member's department (`manage_staff`) is audited with before/after and notifies the staff member,
  because department drives request and announcement scope.
* `RequestCategory.approval_capability` ∈ {`approve_requests`, `approve_transfers`}; a category with `is_transfer`
  must use `approve_transfers` (DB CHECK).
* Registration override (`can_override_registration`, holders of `manage_students`): register/drop on a student's
  behalf outside the registration window only; prerequisites, capacity and credit limits still apply; a reason is
  mandatory and audited.

### 5.3 Authorization matrix (default configuration)

Legend: ✔ allowed · own = only own records · scoped = only within assignment/department/offering ·
cap = requires the named capability (default groups above) · ✖ denied (403/404).

| Operation | Student | Staff / Lecturer | Admin | Superadmin |
|---|---|---|---|---|
| Login, logout, change own password, own settings | ✔ | ✔ | ✔ (MFA) | ✔ (MFA) |
| View own dashboard/profile | own | own | own | own |
| Edit own contact fields, photo, emergency contact | own | own (contact) | own | own |
| Edit institutional student fields (ID, program, status, admission…) | ✖ | ✖ | cap `manage_students` | cap |
| View another student's profile | ✖ | scoped: name/ID/program of students in own offerings; no contacts | cap `manage_students` (full) | cap |
| View grades | own | scoped (own offerings, cap `record_grades`) | cap `manage_grades` | cap |
| Browse units / offerings | ✔ | ✔ | ✔ | ✔ |
| Register / drop units | own, rules enforced | ✖ | override outside window only, cap `manage_students`, reason required | same |
| Manage units/offerings/prerequisites | ✖ | ✖ | cap `manage_units` | cap |
| View timetable | own (registered offerings) + public master | own teaching + master | ✔ | ✔ |
| Create/modify timetable | ✖ | cap `manage_timetable` | cap | cap |
| Browse hostels, rules, availability | ✔ | ✔ | ✔ | ✔ |
| Apply / book / accept / cancel hostel | own, window rules | ✖ | ✖ | ✖ |
| Manage hostels, allocate, transfer, vacate | ✖ | ✖ | cap `manage_hostels` | cap |
| Browse clubs; request/leave membership | ✔ / own | browse | browse | browse |
| Approve memberships | club officer with `can_manage_members` (never own membership) | club's advisor | cap `manage_clubs` | cap |
| Appoint club officers / edit club info | ✖ | club's advisor | cap `manage_clubs` | cap |
| Create request / transfer request | own | ✖ | ✖ | ✖ |
| View request | own | scoped (assigned / department with `review_requests`) | cap `review_requests` (+`review_all_requests`) | cap |
| Reply / upload to request | own, while open | scoped | cap | cap |
| Change status, assign, request info | ✖ (only cancel own while SUBMITTED/NEEDS_INFORMATION) | scoped + cap | cap | cap |
| Approve/reject request | ✖ | scoped + cap `approve_requests` | cap | cap |
| Approve transfer / execute transfer | ✖ | ✖ | cap `approve_transfers` / `execute_transfers` | cap |
| Publish announcements | ✖ | cap; scope limited to own department / own offerings; INDIVIDUAL only to students in own offerings | cap (any scope) | cap |
| View announcements | audience-matched | audience-matched + own authored | audience-matched + own authored; management list with cap `publish_announcements` (INDIVIDUAL items only to recipients and author) | same as admin |
| Notifications | own | own | own | own |
| Manage STUDENT/STAFF accounts without capabilities (activate, deactivate, send reset code, MFA reset) | ✖ | ✖ | cap `manage_user_accounts` | cap |
| Same for any MFA-required account | ✖ | ✖ | ✖ | cap `manage_roles` |
| Manage request categories/routing | ✖ | ✖ | cap `manage_request_config` | cap |
| Faculties/departments/programs/semesters; staff records | ✖ | ✖ | cap `manage_academics` / `manage_staff` | cap |
| Admin statistics dashboard | ✖ | ✖ | cap `view_statistics` | cap |
| Manage ADMIN/SUPERADMIN accounts, roles, capabilities | ✖ | ✖ | ✖ | cap `manage_roles` |
| View audit logs / security events | ✖ | ✖ | cap `view_audit_logs` | cap |
| Modify/delete audit logs | ✖ | ✖ | ✖ | ✖ (no code path; DB trigger + privileges) |
| System settings, backups | ✖ | ✖ | ✖ | cap |
| `/api/v1/*` | same rules as the equivalent UI action | same | same | same |

### 5.4 Request state machine (D8)

| From → To | Who (all also need the object policy: own request, or reviewer in scope) | Notes |
|---|---|---|
| SUBMITTED → CANCELLED | student (owner) | |
| NEEDS_INFORMATION → UNDER_REVIEW | system, when the owner posts a reply | student never sets status directly |
| NEEDS_INFORMATION → CANCELLED | student (owner) | |
| SUBMITTED → UNDER_REVIEW | reviewer (`review_requests`) | assignment may happen in the same step |
| UNDER_REVIEW → NEEDS_INFORMATION | reviewer | message to student mandatory |
| UNDER_REVIEW → RESOLVED | reviewer | only if `category.requires_approval = false`; resolution text mandatory |
| UNDER_REVIEW → APPROVED / REJECTED | holder of `category.approval_capability`, not the submitter | only if `requires_approval = true`; decision recorded (`decided_by/at`) |
| RESOLVED → UNDER_REVIEW | reviewer (re-open) | |
| SUBMITTED / UNDER_REVIEW / NEEDS_INFORMATION → CLOSED | reviewer | e.g. duplicate; note mandatory |
| RESOLVED / APPROVED / REJECTED → CLOSED | reviewer or scheduled job after 14 days | approved transfers with `executed_at IS NULL` are never auto-closed and cannot be closed manually until executed or formally withdrawn by an `approve_transfers` holder |
| CLOSED, CANCELLED | terminal | messages/attachments rejected |

Every transition: row lock on the request, `RequestStatusChange` row, audit event, notification to the student.

---

## 6. Threat model (STRIDE)

Assets: student PII and contacts, grades/academic status, hostel allocations, request documents,
credentials/MFA secrets, audit trail, admin capabilities, availability of registration/booking windows.
Trust boundaries: browser ↔ Nginx; Nginx ↔ Django; Django ↔ PostgreSQL/Redis; Django ↔ SMTP; operators ↔ hosts/backups.
Actors: anonymous internet user, authenticated student (possibly malicious), staff, admin (insider), compromised
account, external attacker with stolen credentials.

| # | Threat (STRIDE) | Example | Mitigations | Verified by (phase) |
|---|---|---|---|---|
| T1 | Spoofing – credential stuffing / brute force | bot tries leaked passwords | Argon2id, subject/IP/device-cookie throttling with back-off (no indefinite lock), generic errors, MFA for all privileged accounts, security events/alerts | rate-limit & lockout tests (3, 13) |
| T2 | Spoofing – MFA bypass | enroll new device from pre-auth session; replay a code | enrollment only with out-of-band one-time code, step-counter replay block, atomic recovery-code use, throttling, MFA gate middleware | A-1 regression (1), MFA suite (3) |
| T3 | Spoofing – session fixation / hijack | attacker plants session id; stolen cookie | key rotation at login/MFA, HttpOnly/Secure/SameSite, short idle+absolute timeouts, logout flush | session tests (3, 14) |
| T4 | Tampering – IDOR/BOLA | change UUID/request number in URL or POST | scoped selectors, object policies, 404 out-of-scope, identity from session only | IDOR suite (every phase, 14) |
| T5 | Tampering – mass assignment / parameter tampering | add `role=ADMIN`, `status=APPROVED`, `program` to POST/JSON | explicit form/serializer field allowlists, services ignore unknown fields, state machine | tamper tests (3–11, 14) |
| T6 | Elevation – privilege escalation | admin grants self `manage_roles`; student hits admin URL | capability ceilings, no self-role-change, decorators on every admin view, authz matrix tests | matrix suite (3, 13) |
| T7 | Tampering – race conditions | two students book last bed; parallel registrations exceed cap | `select_for_update` on bed/offering/student rows + partial unique indexes; IntegrityError handled | concurrency tests on PostgreSQL (5, 7, 13) |
| T8 | Tampering – SQL injection | `q=' OR 1=1--` | ORM only; raw SQL forbidden (lint rule); parameterised search | injection tests (13) |
| T9 | Tampering/Info – XSS | `<script>` in request text/announcement | autoescaping, no `|safe` on user content, strict CSP with nonces, no `innerHTML` with user data | stored/reflected XSS tests (13) |
| T10 | Tampering – CSRF | cross-site POST approving a request | CsrfViewMiddleware everywhere, no `csrf_exempt`, SameSite cookies, POST-only state changes | CSRF tests (13) |
| T11 | Tampering – malicious upload / path traversal | `../../x.pdf`, PHP in `.jpg`, PDF with JS | size/extension/signature checks, image re-encode, PDF active-content rejection, random server names, private storage, authz download, `nosniff` | upload suite (9, 13) |
| T12 | Repudiation | admin denies approving a transfer | append-only audit (actor, action, object, before/after, IP, UA, request id); DB trigger; app role lacks UPDATE/DELETE; chained periodic seals shipped off-host; denials recorded on independent connection (D16) | audit immutability tests (2, 13) |
| T13 | Info disclosure – errors/logs | stack trace, SQL error, password in log | DEBUG off in prod (settings test), custom error pages, log redaction filter, never log secrets | settings & logging tests (1, 12) |
| T14 | Info disclosure – enumeration | login/reset reveal account existence; sequential IDs | uniform responses/timing, UUIDs, pagination caps, 404 for out-of-scope | enumeration tests (3, 14) |
| T15 | Info disclosure – privacy | lecturer sees student phone numbers | field-level selectors per role, minimal serializers | privacy tests (4, 13) |
| T16 | DoS | huge bodies, unbounded queries, expensive hashing | Nginx/Django body limits, paginated lists, query param limits, password max length, rate limits | (12, 14) |
| T17 | Open redirect | `?next=https://evil` | `url_has_allowed_host_and_scheme`, relative-only | redirect tests (3) |
| T18 | SSRF | user-supplied URLs fetched server-side | no feature fetches user-supplied URLs; notification links restricted to internal paths | design + test (11) |
| T19 | Clickjacking | portal framed | `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'` | header tests (1) |
| T20 | Secrets exposure | key committed to git; default DB password | env-only secrets, prod settings refuse missing secrets, `.env` ignored, secret scan in pre-commit & CI | secret scan (1, every commit) |
| T21 | Insider/DB tampering | app compromise rewrites audit rows | separate DB roles, trigger, audit seal chain + `verify_audit_seals`, backups with retention, restore tests | (2, 15) |
| T22 | Supply chain | malicious/vulnerable dependency, CDN script swap | pinned requirements, `pip-audit` in CI, vendored front-end assets (no CDN), minimal deps | CI (1) |

Out of scope / residual risks (documented, not silently ignored): compromise of an admin's
authenticator device; malicious DB superuser/host root; email account takeover of a student (affects
password reset — mitigated by short token life and notification of password changes); antivirus scanning
is a pluggable hook (ClamAV) that is **off** unless configured.

---

## 7. Cross-cutting conventions

* **Audit event** fields: id, timestamp, actor (FK + cached identifier/role), action (`DOMAIN.VERB`),
  object type/id, before/after JSON (only changed, non-secret fields), ip, user-agent (truncated), request id, outcome.
* **Security events**: LOGIN_SUCCESS/FAILURE, LOGOUT, LOCKOUT/THROTTLED, PASSWORD_RESET_REQUESTED/COMPLETED,
  PASSWORD_CHANGED, MFA_* , PERMISSION_DENIED, RATE_LIMITED, UPLOAD_REJECTED, ROLE_CHANGED.
* **Logging**: JSON lines to stdout; redaction filter removes keys matching
  `password|passwd|secret|token|otp|code|cookie|session|authorization|csrf` (case-insensitive).
* **Errors**: 400/401(→login)/403/404/429/500 templates; no technical detail in responses.
* **Time**: UTC in DB, `Africa/Nairobi` default display time zone (configurable).

---

## 8. Requirements traceability (spec section → design element)

| Spec § | Covered by |
|---|---|
| 3, 18, 34 | §5 capability ceilings, policies, matrix |
| 4, 22 | §4.1–4.4 |
| 5–6 | selectors scoped to `request.user`; profile field split (DATABASE.md) |
| 7, 32 | UnitOffering, registration service with locks + constraints |
| 8 | Venue/TimetableEntry, conflict validation + exclusion-style overlap checks |
| 9, 32 | Hostel hierarchy, windows, allocation service, partial unique indexes |
| 10 | Club(kind), ClubMembership approvals, officer appointment only by `manage_clubs` holders or the club's advisor, never self |
| 11–13, 40 | StudentRequest + state machine (D8), TransferRequest (D9), admin panel |
| 14–15 | Notification, Announcement with audience resolution |
| 16 | DATABASE.md |
| 17, 19–21, 29–31, 33 | SECURITY.md controls + tests |
| 23–26 | env-only secrets, structured logging, AuditLog, privacy selectors |
| 27–28 | DB roles (D14), BACKUP_AND_RESTORE.md with restore test |
| 35–37 | TESTING.md, SECURITY_TEST_REPORT.md |
| 41–42 | responsive Tailwind layout, navigation as specified |
| 44 | git with conventional commits, pre-commit secret scan |

---

## 9. Development phases & exit criteria

Every phase ends with: full test suite green on PostgreSQL, `manage.py check --deploy` (prod settings),
ruff + bandit + pip-audit + secret scan clean, docs updated, conventional commit(s).

| Phase | Scope | Exit criteria (beyond the common ones) |
|---|---|---|
| 1 | Architecture & requirements; foundation hardening | Design artifacts; Django 5.2 LTS; env-only secrets; settings split verified by tests; trusted-proxy IP; containment of A-1/A-2/F-1; Docker with least-privilege DB roles; CI + pre-commit; secret scan |
| 2 | Models & migrations | All models of DATABASE.md; constraints tested (unique/partial/check); audit append-only trigger + ORM guard tested; capability catalog migration |
| 3 | Authentication & authorization | Login/logout/reset/change; throttling; MFA (enroll, verify, replay, recovery, reset); session policy; authz layer + matrix tests |
| 4 | Student dashboard & profile | Scoped dashboard; profile edit allowlist; photo upload pipeline; IDOR tests |
| 5 | Units & registration | Catalogue/search/filter; register/drop rules; concurrency tests (capacity, credit cap, duplicates) |
| 6 | Timetable | Student/lecturer/master views; admin CRUD; room/lecturer/group conflict tests |
| 7 | Hostels | Browse, apply, book, accept, cancel; admin allocate/transfer/vacate; last-bed race test |
| 8 | Clubs & societies | Browse/search; membership request/approve/leave; leader appointment rules |
| 9 | Requests & transfers | State machine; attachments (secure upload/download); transfer approve + execute (idempotent) |
| 10 | Admin panel | Dashboard stats; user/staff/academic/hostel/club management UIs; audit viewer; all capability-gated |
| 11 | Notifications & announcements | Event notifications; audience targeting; read/unread; optional email outbox |
| 12 | Security hardening | CSP nonces, vendored assets, headers, body limits, logging redaction, error pages |
| 13 | Automated testing | Coverage of all spec §35 categories; coverage report |
| 14 | Adversarial testing | Spec §36 attack list executed; fixes + regression tests; SECURITY_TEST_REPORT.md complete |
| 15 | Deployment preparation | Nginx config, production compose, backup/restore scripts with a passing restore test, secret rotation runbook |

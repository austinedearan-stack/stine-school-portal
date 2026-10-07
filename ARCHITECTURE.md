# Architecture Documentation

## 1. Overview
The University Student Portal is designed as a secure, maintainable, modular system based on Django 5.x and Django REST Framework. The design follows the principle of **Defense-in-Depth**:
- The client browser is untrusted.
- Authorization is checked centrally in backend policy services (`apps.core.permissions`).
- Data integrity is enforced using relational database constraints, transactions, and row-level locks.
- Administrative operations create immutable audit events.

```
                    ┌──────────────────────────────────────────────┐
                    │               Web Browser                    │
                    │   (Tailwind CSS + Alpine.js / HTMX)         │
                    └──────────────────────┬───────────────────────┘
                                           │ HTTPS (Secure Cookie, CSRF)
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │              Django Middleware               │
                    │  - Security Headers (CSP, HSTS, X-Frame)     │
                    │  - Rate Limiting / Lockout Tracker           │
                    │  - Session Rotation & Authentication         │
                    │  - Immutable Audit Logging Middleware        │
                    └──────────────────────┬───────────────────────┘
                                           │
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │         Centralized Authorization            │
                    │        (apps.core.permissions)               │
                    │  can_view_student(), can_book_hostel(), ...  │
                    └──────────────────────┬───────────────────────┘
                                           │
                 ┌─────────────────────────┼─────────────────────────┐
                 ▼                         ▼                         ▼
        ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
        │   Accounts &    │       │   Academics &   │       │  Hostels & Bed  │
        │  MFA Service    │       │  Registration   │       │   Allocation    │
        └────────┬────────┘       └────────┬────────┘       └────────┬────────┘
                 │                         │                         │
                 └─────────────────────────┼─────────────────────────┘
                                           │ Transactional / Row Locks
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │      PostgreSQL (Prod) / SQLite (Dev)        │
                    │    - Relational constraints (FK, Unique)     │
                    │    - Immutable AuditLog & SecurityEventLog   │
                    └──────────────────────────────────────────────┘
```

## 2. Modular App Decomposition
The application is structured into domain-driven Django apps under `apps/`:

1. **`accounts`**:
   - Manages identity, authentication, session security, password policies, TOTP-based Multi-Factor Authentication (MFA), and profiles (`StudentProfile`, `StaffProfile`).
2. **`core`**:
   - Central authorization policies, security middleware, file upload safety (MIME/magic byte inspection), rate-limiting utilities, custom error handlers, and immutable audit logs (`AuditLog`, `SecurityEventLog`).
3. **`academics`**:
   - Academic catalog (`Faculty`, `Department`, `Program`, `Unit`, `AcademicYear`, `Semester`, `UnitPrerequisite`).
   - Transactional unit registration with concurrency protection and credit cap enforcement.
4. **`timetable`**:
   - Schedule management (`TimetableEntry`, `Classroom`).
   - Multi-dimensional clash detection algorithms (room, lecturer, student group conflicts).
5. **`hostels`**:
   - Residential management (`Hostel`, `HostelBuilding`, `Room`, `Bed`, `HostelApplication`, `HostelAllocation`).
   - Race-condition-safe bed allocation using `select_for_update()`.
6. **`clubs`**:
   - Extracurricular organizations (`Club`, `ClubMembership`, `ClubEvent`).
   - Membership application and approval workflows.
7. **`requests`**:
   - Generalized request and ticketing workflow (`RequestCategory`, `StudentRequest`, `RequestMessage`, `RequestAttachment`).
   - Dedicated university transfer management (`TransferRequest`).
8. **`notifications`**:
   - In-app notification dispatcher and targeted institutional announcements (`Announcement`).
9. **`administration`**:
   - Unified administrative cockpit, real-time KPI aggregates, user lifecycle management, and audit log search.

## 3. Session & Credential Security
- **Hashing**: Argon2 (`Argon2PasswordHasher`) by default.
- **Session Lifecycle**:
  - Keys cycled on authentication via `session.cycle_key()` to prevent session fixation.
  - Expiration: Strict idle timeout (30 min for students, 15 min for administrators).
- **MFA Enforcement**:
  - Compulsory for `ADMIN` and `SUPERADMIN` roles using RFC 6238 TOTP.
  - Rate-limited verification attempts to prevent brute-forcing 6-digit codes.
  - Single-use hashed backup recovery codes.

## 4. Concurrency & Integrity Strategy
- Resource booking operations that cannot exceed capacity (Hostel Beds and Unit Registration caps) MUST execute inside `transaction.atomic()`.
- Records are locked using `select_for_update()` before capacity checks to eliminate race conditions between simultaneous requests.
- Database unique constraints act as the absolute final safeguard against double allocations.

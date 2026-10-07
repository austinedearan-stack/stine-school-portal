# Security Policy & Implementation Reference

## 1. Baseline Principles
This university portal follows OWASP Top 10 and OWASP ASVS guidelines. Every security control is enforced at the backend and database layer; the client web browser is treated as hostile.

## 2. Authentication Controls
- **Password Hasher**: Argon2 (`Argon2PasswordHasher`) algorithm with high memory and iteration parameters.
- **Password Complexity**: Minimum 12 characters, requiring uppercase, lowercase, digit, and special character.
- **Session Protection**:
  - `SESSION_COOKIE_HTTPONLY = True` (mitigating XSS theft).
  - `SESSION_COOKIE_SAMESITE = 'Lax'` (mitigating CSRF).
  - `SESSION_COOKIE_SECURE = True` in production (enforcing HTTPS transport).
  - `request.session.cycle_key()` invoked immediately upon authentication to prevent session fixation.
- **Brute Force & Progressive Lockout**:
  - Accounts lock for 15 minutes after 5 consecutive failed login attempts.
  - Generic authentication error messages prevent user enumeration.
- **Multi-Factor Authentication (MFA)**:
  - RFC 6238 TOTP required for `ADMIN` and `SUPERADMIN` roles.
  - Single-use hashed backup codes for emergency recovery.
  - Rate limiting on MFA code submission to eliminate automated guessing.

## 3. Authorization (RBAC) & IDOR Prevention
- Access control is centralized in `apps.core.permissions`.
- Zero trust in query parameters: endpoints derive user identity from `request.user` rather than trusting client-provided IDs.
- Ownership checks on every student-specific resource (applications, requests, registrations).

## 4. File Upload Security
- Strict extension whitelist (`.jpg`, `.jpeg`, `.png`, `.pdf`).
- MIME type validation and file signature (magic byte) inspection.
- Files renamed to UUIDs upon upload; original untrusted filenames are discarded.
- Maximum file size capped at 2.5 MB.
- Static media served via separate paths with execution disabled.

## 5. Defense Against Common Web Vulnerabilities
- **SQL Injection**: Django ORM parameterized queries used exclusively; no raw string interpolation.
- **Cross-Site Scripting (XSS)**: Automatic context-aware escaping in Django templates; strict CSP headers.
- **Cross-Site Request Forgery (CSRF)**: CSRF tokens verified on all state-changing requests (POST, PUT, PATCH, DELETE).
- **Clickjacking**: `X-Frame-Options: DENY` and CSP `frame-ancestors 'none'`.
- **Race Conditions**: Database-level `select_for_update()` inside `transaction.atomic()` blocks for bed and unit bookings.

## 6. Security Headers (Production)
```http
Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; font-src 'self' https://cdn.jsdelivr.net; frame-ancestors 'none';
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
```

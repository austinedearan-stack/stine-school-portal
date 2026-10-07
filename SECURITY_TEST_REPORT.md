# Security Test Report

Security controls implemented and tested against the defined test cases. This does **not** mean the
system is secure: most features are not yet built (see README status). The report grows each phase;
Phase 14 (adversarial testing) executes the full spec §36 attack list.

Environment for Phase 1 results: Python 3.13.16, Django 5.2.18, PostgreSQL 16.15 and SQLite, 2026-10-07.

| # | Vulnerability tested | Attack method | Expected | Actual (before fix) | Fix | Regression test |
|---|---|---|---|---|---|---|
| P1-01 | MFA bypass via enrollment (A-1, critical) | Correct password → pre-MFA session → GET/POST `/accounts/mfa/setup/` with attacker TOTP secret | Enrollment refused, not logged in, event recorded | Attacker enrolled own device and was logged in | Pre-auth enrollment blocked when a device exists; logged-in replacement refused (403) until Phase 3 re-auth flow | `tests/security/test_mfa_enrollment_bypass.py` (4 tests) |
| P1-02 | Admin MFA bypass via stock Django admin (A-2, critical) | Browse `/django-admin/` and log in with password only | 404 | Password-only admin login available | Admin unmounted unless `DEBUG` + explicit flag; production refuses flag | `tests/security/test_admin_and_media_exposure.py`, `test_production_settings.py` |
| P1-03 | Rate-limit / audit IP spoofing (I-1) | Send `X-Forwarded-For: 1.2.3.4` | Header ignored without declared proxy; right-most-trusted hop used with proxies | Forged IP used for rate-limit keys and audit | `apps/core/net.get_client_ip` | `tests/security/test_client_ip.py` (6) |
| P1-04 | Hard-coded / weak secret key (A-3) | Start production without or with weak `DJANGO_SECRET_KEY` | Refuse to start | Fell back to a hard-coded key | Fail-fast production settings | `test_unsafe_configuration_is_refused` (13 cases) |
| P1-05 | Insecure production configuration | Missing hosts, `*`, SQLite, DB superuser, no DB password, `sslmode=prefer`, no Redis, http CSRF origin | Refuse to start | Several silently accepted | Same | same |
| P1-06 | Public exposure of private uploads (F-1) | Request `/media/...` and `MEDIA_URL` paths with DEBUG on/off | 404 | Served without authorization in DEBUG | Removed static media serving; uploads stored outside web roots | `test_uploaded_media_is_never_served_publicly` |
| P1-07 | Information disclosure in errors | Trigger an unhandled exception containing SQL/path text; request unknown URL | Generic page, no details | 500 page depended on base template (DB access) | Standalone 500 template rendered without request context | `test_500_page_hides_exception_details`, `test_custom_404_has_no_debug_information` |
| P1-08 | CSRF | POST login without token | 403, generic text | — (already enforced) | CSRF failure view hides reason | `test_login_without_csrf_token_rejected_generically` |
| P1-09 | Clickjacking / MIME sniffing | Inspect response headers | DENY, `frame-ancestors 'none'`, nosniff | Present | — | `test_security_headers_present` |
| P1-10 | Secrets in logs | Log messages/extra fields containing password, token, session id, MFA secret | Redacted | No redaction existed | `RedactSecretsFilter` + JSON formatter | `tests/security/test_logging_redaction.py` (4) |
| P1-11 | Secrets committed to the repo | Scan tracked files; plant AWS key + password literal | Clean repo; planted secrets detected | `.env.example` had a `django-insecure` key; compose had a default DB password | Placeholders only; compose requires values; scanner in tests/pre-commit/CI | `tests/test_repo_hygiene.py` |
| P1-12 | Dangerous patterns | Search for `csrf_exempt`, raw SQL, `mark_safe`, `|safe` | None | None found | Guard test added | `test_no_csrf_exempt_or_raw_sql_in_apps`, `test_no_safe_filter_in_templates` |
| P1-13 | Database privilege (I-2) | As `portal_app`: `CREATE TABLE`; check `rolsuper` | Denied; not superuser | App ran as PostgreSQL superuser | `deploy/postgres/init/01-roles.sh` | Manual verification on PG16 (2026-10-07): `permission denied for schema public`, `rolsuper=f`. Automated in Phase 2 |
| P1-14 | Module shadowing (S-4) | `import requests` resolved to the portal app | Library import unaffected | Shadowed | App renamed `student_requests`, `sys.path` hack removed | `test_apps_directory_not_injected_into_sys_path` |

## Known open issues (not yet fixed)

All remaining findings in `docs/AUDIT_EXISTING_CODE.md` are open and scheduled. Notably: first-time MFA
enrollment still needs only the password (one-time enrollment codes, Phase 3); per-account lockout can be
abused to lock users out (A-5, Phase 3); password reset is a placeholder (A-4, Phase 3); announcement IDOR
(Z-1/Z-2, Phase 11); unrestricted request status changes (Z-4, Phase 9–10).

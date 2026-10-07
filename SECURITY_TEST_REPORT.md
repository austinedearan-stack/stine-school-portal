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

## Phase 2–3 results

Environment: Python 3.14.5, Django 5.2.18, SQLite (PostgreSQL-only tests run in CI), 2026-10-07.

| # | Vulnerability tested | Attack method | Expected | Actual (before fix) | Fix | Regression test |
|---|---|---|---|---|---|---|
| P2-01 | Audit tampering (I-5) | `QuerySet.update/delete`, `bulk_update`, raw `UPDATE`/`DELETE`/`TRUNCATE` on audit tables | Refused at ORM and DB | Only `Model.save/delete` guarded | ORM guard + triggers + privilege revocation | `tests/models/test_append_only.py` |
| P2-02 | Superuser escalation (D4) | `User.objects.update(is_superuser=True)`; `createsuperuser` | Refused | Allowed | DB CHECK; `create_superuser` disabled | `tests/models/test_constraints.py` |
| P3-01 | First-time MFA enrollment with password only (A-1 remainder) | Correct password for an un-enrolled admin, then the enrollment page | QR/secret never shown without a valid out-of-band enrollment code | Password alone enrolled a device | One-time HMAC-stored enrollment codes | `test_first_enrollment_requires_an_out_of_band_code` |
| P3-02 | TOTP replay (A-7) | Re-submit an accepted code; submit an older-step code after a newer one | Refused | Older-step code accepted | Step counter + conditional UPDATE | `test_totp_code_cannot_be_replayed`, `test_older_step_code_is_refused_after_a_newer_one` |
| P3-03 | Brute force / lockout DoS (A-5) | 5 wrong passwords then the right one; 40 failures from many IPs; owner with device cookie | Pair locked with back-off; owner on a known device unaffected | Any account lockable by anyone | HMAC-subject throttle with device-cookie bucket | `tests/auth/test_throttle.py` (9) |
| P3-04 | Account enumeration (A-6) | Compare responses for unknown / wrong password / inactive; reset request for unknown account | Identical | Different messages; no hashing for unknown users | Uniform responses, dummy hash, async mail | `test_failure_responses_are_identical_for_every_cause`, `test_reset_request_response_is_identical_for_unknown_accounts` |
| P3-05 | Limiter outage | Cache raises on every call during login | 503, not logged in | — | Fail-closed limiter | `test_limiter_failure_fails_closed` |
| P3-06 | Open redirect (T17) | `next` pointing off-site (absolute URL, protocol-relative, backslash, `javascript:`) | Redirect to dashboard | — | `safe_next_url` | `test_next_parameter_cannot_redirect_off_site` (4) |
| P3-07 | Session fixation / hijack (T3, A-11) | Pre-login session id reused; idle/absolute timeout; MFA-required session without MFA stamp | New key at login; sessions end; refused | Same key reused across MFA; single timeout | Rotation + SessionPolicyMiddleware | `test_session_key_rotates_on_login`, timeout tests, `test_mfa_required_session_without_mfa_stamp_is_refused` |
| P3-08 | Password reset abuse (A-4) | Reuse code; 5 wrong attempts; code after password change; reset to bypass MFA | All refused; MFA still required | Reset not implemented | Emailed HMAC code, 20 min, 5 attempts, single use | `tests/auth/test_sessions_and_passwords.py` |
| P3-09 | Privilege escalation via role management (T6, Z-6) | Admin in Superadmin group changes roles; self role change; out-of-ceiling group; removing last superadmin; IT support resetting an admin's MFA | All refused | All admins had all powers | Ceilings + `authorize` + invariants under row locks | `tests/auth/test_authz.py` |
| P3-10 | Cross-site logout (A-10) | `GET /accounts/logout/` | 405 | Logged out | POST-only logout | `test_logout_requires_post_and_flushes_session` |

## Known open issues (not yet fixed)

Remaining findings in `docs/AUDIT_EXISTING_CODE.md` are scheduled to the feature phases that rebuild the
affected modules (Z-1/Z-2 announcements, Z-4/Z-5 requests and transfers, Z-7 clubs, F-2/F-3 uploads).

# Security Test Report

Security controls implemented and tested against the defined test cases. This does **not** mean the
system is secure. All modules are implemented (see README status). The report grows each phase;
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

## Phase 14 — adversarial testing (2026-10-08)

Method: (1) an attacker-mindset review of every module's code against the threat model (ARCHITECTURE.md §6);
(2) `tests/adversarial/test_campaign.py` — each attack scripted from the attacker's side (28 tests); (3) a black-box
probe over real HTTP (`scripts/security_probe.py`, 26 checks) against a local development server with seeded fake data,
DEBUG off. Earlier phases' attack tests (P1–P3 above, feature suites, Phase 13 security suites) were re-run unchanged.

| # | Vulnerability tested | Attack method | Expected | Actual (before fix) | Fix | Regression test |
|---|---|---|---|---|---|---|
| P14-01 | **Homoglyph twin accounts / throttle evasion** | Create `Ｓ123` (fullwidth) or `S123е` (Cyrillic) next to `S123`; sign in or guess passwords with look-alike identifiers | Refused; look-alikes resolve to (and are throttled with) the canonical account | **Twin accounts could be created; look-alike identifiers had separate throttle budgets** | NFKC normalisation of identifiers at creation, lookup, throttle subject and identifier hashing; usernames restricted to ASCII (migration `accounts.0004`) | `test_fullwidth_lookalike_username_cannot_create_a_twin_account`, `test_lookalike_identifier_signs_in_to_the_canonical_account_and_shares_its_throttle` |
| P14-02 | Session replay | Reuse a session cookie after the victim logs out | Anonymous | Refused | — | `test_stolen_session_cookie_is_useless_after_logout` |
| P14-03 | Device-cookie forgery | Present a forged trusted-device cookie to escape the lockout | Still throttled | Refused | — | `test_forged_device_cookie_does_not_bypass_the_lockout` |
| P14-04 | Host header injection | `Host: evil.example.com` | 400 | 400 (and reset mails contain no links) | — | `test_unknown_host_header_is_refused` |
| P14-05 | Input abuse | Null bytes, 500-char subjects, 10 000-char searches, 1 MB passwords, negative/huge numbers, page=-1/abc/1e9 | Clean form errors, no 500, no hashing of giant passwords | As expected | — | `test_null_bytes_…`, `test_megabyte_password_…`, `test_negative_and_out_of_range_numbers_…`, `test_pagination_abuse_is_harmless` (5) |
| P14-06 | HTTP verb tampering | GET/PUT/PATCH/DELETE on state-changing endpoints; TRACE | 405 | 405 | — | `test_state_changing_endpoints_refuse_get_and_other_verbs`; probe |
| P14-07 | Hidden-field tampering | Student posts `form=offer` to the accommodation office page | 403 | 403 | — | `test_student_cannot_reach_office_branches_by_tampering_hidden_fields` |
| P14-08 | Sensitive paths / traversal | `/.env`, `/.git/config`, `/admin/`, `/static/../manage.py`, encoded traversal | 404/redirect, nothing disclosed | 404 through WhiteNoise. Only Django's **development** static handler (`runserver --insecure`) answered 500 to `/static/../manage.py` (SuspiciousFileOperation, nothing disclosed) | Not applicable to production (WhiteNoise serves static; Nginx in front); documented | `test_sensitive_paths_are_not_served` (9); probe |
| P14-09 | Self-escalation | Superadmin/admin changes own groups; open redirect via re-authentication `next` | Refused | Refused | — | `test_superadmin_cannot_change_own_groups_…`, `test_open_redirect_through_reauthentication_next_is_refused` |
| P14-10 | Enrollment-code brute force | Guess codes after a stolen password | Pre-auth discarded after 5 tries | As expected | — | `test_enrollment_code_guessing_is_cut_off` |
| P14-11 | Script in upload filename | `"><script>….png` | Sanitised name, escaped output | As expected | — | `test_script_in_attachment_filename_is_neutralised` |
| P14-12 | Version disclosure | Inspect `Server` header | No version | Development server reports `WSGIServer/0.2 CPython/…` | Production: Nginx `server_tokens off` and hides the upstream header (Phase 15) | probe check "no Server version leak" |

Black-box probe result (DEBUG off, static via WhiteNoise): 25/26 checks pass; the remaining one is P14-12 (development
server only). Headers, CSRF on the login form, generic failures, throttling (429 after 5 failures), API refusal,
request-id sanitising, 404 page without debug detail, and refusal of sensitive paths were all confirmed over HTTP.

Accepted residual risks (documented, not fixed):
* An attacker who already knows a user's password can exhaust that user's MFA attempt budget (10 per 15 minutes),
  delaying the real user's sign-in by up to 15 minutes; the password should be changed in that case.
* Student and staff IDs and email addresses have separate throttle budgets (by design, so throttling is not a
  linking oracle; ARCHITECTURE.md §4.1).
* Audit records keep the before/after values of contact fields; readable only with `view_audit_logs`.
* Race and trigger tests run on PostgreSQL in CI only; they were not executed in this environment.

## Known open issues (not yet fixed)

Remaining findings in `docs/AUDIT_EXISTING_CODE.md` are scheduled to the feature phases that rebuild the
affected modules (Z-1/Z-2 announcements, Z-4/Z-5 requests and transfers, Z-7 clubs, F-2/F-3 uploads).

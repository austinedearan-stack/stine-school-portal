# Security

Baseline references: OWASP Top 10 (2021) and OWASP ASVS 4.0. Wording rule for this project: we say
"security controls implemented and tested against the defined test cases", never "secure".

Design: `ARCHITECTURE.md` §4 (authentication), §5 (authorization), §6 (threat model);
`DATABASE.md` §2.8 and §3 (audit immutability, locking). Inherited-code findings:
`docs/AUDIT_EXISTING_CODE.md`. Test evidence: `SECURITY_TEST_REPORT.md`.

## Control status

✅ implemented + tested · 🟡 partially (containment only) · ⏳ designed, scheduled (phase)

| Area | Control | Status |
|---|---|---|
| Secrets | No secret has a default; production refuses missing/weak `DJANGO_SECRET_KEY`, missing DB password, superuser DB account, `*` hosts | ✅ 1 |
| Secrets | `.env` git-ignored; `.env.example` placeholders only; secret scanner in tests, pre-commit, CI; gitleaks in pre-commit | ✅ 1 |
| Transport | HTTPS redirect, HSTS 1 year (preload opt-in), `__Host-` Secure cookies, DB `sslmode=require` by default | ✅ 1 |
| Headers | nosniff, `X-Frame-Options: DENY`, strict CSP (`'self'` only, no inline script/style, `object-src`/`frame-src`/`base-uri` none, `upgrade-insecure-requests`), Referrer-Policy, COOP, CORP, Permissions-Policy, `no-store` for authenticated pages | ✅ 12 |
| Proxy trust | `X-Forwarded-For`/`-Proto` ignored unless `TRUSTED_PROXY_COUNT` declared; right-most-trusted parsing | ✅ 1 |
| Admin surface | Stock Django admin never mounted unless `DEBUG` and explicitly enabled; production refuses the flag | ✅ 1 |
| Files | Uploaded files never served from a public URL | ✅ 1 (authorized downloads 9) |
| Errors | Custom 404/500 without technical detail; 500 page renders with no DB/context dependencies; CSRF failure page generic | ✅ 1 (400/401/403/429 reviewed in 12) |
| Logging | JSON logs, redaction of password/token/cookie/session/OTP keys and inline `key=value` secrets | ✅ 1 |
| MFA | TOTP for every admin/superadmin and capability holder; first enrollment only with an out-of-band one-time enrollment code (A-1); encrypted secrets (A-9); step-counter replay guard (A-7); HMAC single-use recovery codes; per-session and per-user attempt limits (A-8); replacement only after password + factor re-authentication, old device kept until the new one is confirmed | ✅ 3 |
| Database | Separate owner/runtime roles; runtime role cannot run DDL; statement & idle-transaction timeouts | ✅ 1 (verified manually on PG16; audit-table privilege revocation in 2) |
| Supply chain | Pinned dependencies, `pip-audit` in CI, no CDN and no third-party JavaScript at runtime | ✅ 1, 12 |
| DoS | Request-size cap (413), paginated lists, bounded form fields, password max length, per-user/IP cap on state-changing requests | ✅ 12 |
| AuthN | Argon2id; HMAC-subject throttling (IP / subject+IP back-off / per-subject slow-down / device-cookie bucket), fail-closed limiter (A-5, A-6); identical responses for unknown, wrong-password and inactive accounts; reset by emailed one-time code in a POST form (A-4); session key rotation at login and MFA; idle 30/15 min and absolute 12/4 h lifetimes (A-11); POST-only logout (A-10); MFA gate middleware; forced password change; password change ends other sessions, revokes trusted devices and reset codes, notifies the user | ✅ 3 |
| AuthZ | Capability catalog with role ceilings; `authorize()` + named policies + decorators; denials recorded on an independent connection; role/group/activation invariants (no self-change, grants stripped on role change, ceiling enforced at grant time, last capable superadmin protected under row locks) | ✅ 3 (object policies and scoped selectors per feature phase) |
| Audit | Append-only (ORM + trigger + privileges), server-assigned sequence, hash-chained seals with `verify_audit_seals` | ✅ 2, 12 |
| Races | Row locks (documented order, `of=self`) + partial unique / exclusion constraints, PostgreSQL concurrency tests | ✅ 5, 7, 9 (tests run on PostgreSQL in CI) |
| Uploads | Size/extension/signature checks, image re-encoding, PDF active-content rejection (incl. compressed streams), private storage, authorized downloads with sandbox CSP | ✅ 4, 9 |
| Deployment | Only Nginx exposed (TLS 1.2/1.3, version hidden, unknown hosts refused, body/rate limits, internal X-Accel location for private files); read-only, capability-dropped containers; internal data network; owner credentials only in migrate/backup | ✅ 15 (static tests + `nginx -t`/compose validation in CI) |
| Backups | Nightly snapshot-consistent backups with manifest; weekly automated restore test (checksums, row counts, seal chains, uploads); encrypted off-site copies; status visible to SUPERADMINs | ✅ 15 |
| Key rotation | Fallback lists for the secret key and the HMAC key; MultiFernet for MFA secrets | ✅ 15 |

## Reporting a vulnerability

Report privately to the project owner (do not open a public issue). Include steps to reproduce.
Never include real student data in a report.

## Secret rotation (summary — full runbook in DEPLOYMENT.md §8)

* `DJANGO_SECRET_KEY` and `PORTAL_HMAC_KEY`: new key in place, old key in `…_FALLBACKS` for a transition
  period, then removed. Codes, recovery codes and device cookies keep working during the transition.
* `MFA_ENCRYPTION_KEYS`: prepend the new key, run `manage.py rotate_mfa_encryption`, drop the old key.
* Database and Redis passwords: `ALTER ROLE …` / new `requirepass`, update the env files, restart.
* TLS and backup-encryption keys: see the runbook.

## Incident response (outline)

1. Contain: rotate affected secrets (DEPLOYMENT.md §8), end all sessions, deactivate affected accounts.
2. Preserve evidence: do not restart or rebuild hosts before taking copies of logs; run `verify_audit_seals`.
3. Investigate with the audit log and security events (Administration → Audit log / Security events).
4. Notify the institution's data protection officer at once. Personal-data breaches may have to be
   reported to the Data Commissioner within 72 hours (Kenya Data Protection Act, 2019); the DPO decides.
5. Recover (BACKUP_AND_RESTORE.md §5 if data was altered), then add a regression test and a
   SECURITY_TEST_REPORT.md entry for the root cause.

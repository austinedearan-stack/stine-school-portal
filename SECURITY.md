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
| Headers | nosniff, `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`, Referrer-Policy, COOP | ✅ 1 (CSP tightened with nonces in 12) |
| Proxy trust | `X-Forwarded-For`/`-Proto` ignored unless `TRUSTED_PROXY_COUNT` declared; right-most-trusted parsing | ✅ 1 |
| Admin surface | Stock Django admin never mounted unless `DEBUG` and explicitly enabled; production refuses the flag | ✅ 1 |
| Files | Uploaded files never served from a public URL | ✅ 1 (authorized downloads 9) |
| Errors | Custom 404/500 without technical detail; 500 page renders with no DB/context dependencies; CSRF failure page generic | ✅ 1 (400/401/403/429 reviewed in 12) |
| Logging | JSON logs, redaction of password/token/cookie/session/OTP keys and inline `key=value` secrets | ✅ 1 |
| MFA | Pre-auth session can no longer enroll a new device on an enrolled account (A-1) | 🟡 1 → full design in 3 |
| Database | Separate owner/runtime roles; runtime role cannot run DDL; statement & idle-transaction timeouts | ✅ 1 (verified manually on PG16; audit-table privilege revocation in 2) |
| Supply chain | Pinned dependencies, `pip-audit` (0 known vulns at Phase 1), no CDN at runtime (12) | ✅ 1 / ⏳ 12 |
| AuthN | Argon2id, throttling with device cookies, generic errors, reset by emailed code, session rotation/timeouts | ⏳ 3 |
| AuthZ | Capability catalog with role ceilings, object policies, scoped selectors, 404 for out-of-scope | ⏳ 3 |
| Audit | Append-only (ORM + trigger + privileges), sealed hash chain | ⏳ 2 |
| Races | Row locks + partial unique / exclusion constraints, PostgreSQL concurrency tests | ⏳ 5, 7 |
| Uploads | Size/extension/signature checks, image re-encoding, PDF active-content rejection, private storage | ⏳ 9 |

## Reporting a vulnerability

Report privately to the project owner (do not open a public issue). Include steps to reproduce.
Never include real student data in a report.

## Secret rotation (summary — full runbook in DEPLOYMENT.md)

* `DJANGO_SECRET_KEY`: rotate by setting the new key and moving the old one to
  `DJANGO_SECRET_KEY_FALLBACKS` (Phase 3 wires this) so sessions survive one cycle; remove the fallback later.
* Database passwords: `ALTER ROLE … PASSWORD …` then update the secret store and restart.
* `MFA_ENCRYPTION_KEYS` (Phase 3): prepend the new key; run the re-encryption command; drop the old key.
* After any suspected leak: rotate, invalidate all sessions (`clearsessions` + truncate session table), review audit/security logs.

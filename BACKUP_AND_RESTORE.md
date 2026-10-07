# Backup and Restore

Status: **strategy defined; scripts and the automated restore test are delivered in Phase 15.**
A backup that has never been restored is **not** considered verified.

## What is backed up

| Data | Method | Frequency | Retention |
|---|---|---|---|
| PostgreSQL database | `pg_dump --format=custom` as `portal_owner` (+ WAL archiving for point-in-time recovery in production) | nightly full; WAL continuous | 7 daily, 4 weekly, 12 monthly |
| Private uploads (`PRIVATE_MEDIA_ROOT`) | `tar` + SHA-256 manifest | nightly | same as database |
| Configuration | infrastructure-as-code in git; secrets in the secret store (never in backups of the repo) | on change | git history |

Backups are encrypted at rest (e.g. `age`/GPG with a key held outside the backup location), copied
off-host, and readable only by the backup operator role. They contain personal data: access is logged and
restricted (`manage_backups` in the portal can trigger/verify, never download).

## Restore procedure (outline)

1. Provision an empty PostgreSQL 16 instance and run `deploy/postgres/init/01-roles.sh`.
2. `pg_restore --no-owner --role=portal_owner -d school_portal <dump>`; re-apply grants (script, Phase 15).
3. Extract the uploads archive into `PRIVATE_MEDIA_ROOT`; verify the SHA-256 manifest.
4. `python manage.py migrate --check` and `python manage.py verify_audit_seals` (Phase 2).
5. Smoke test: log in as a test account, open a request attachment, check row counts against the backup manifest.

## Restore test (Phase 15 deliverable)

`scripts/restore_test.sh` will restore the latest backup into a scratch database, run the checks above and
fail loudly on any mismatch. It runs on a schedule; its last result is visible to SUPERADMINs.

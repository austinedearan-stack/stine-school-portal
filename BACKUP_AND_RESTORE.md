# Backup and Restore

Status: **implemented (Phase 15).**

* `manage.py backup_portal` takes backups.
* `manage.py restore_test` restores a backup into a scratch database and verifies it.
* Both run from the `backup` container on a schedule (`DEPLOYMENT.md` §6).
* The results are visible to SUPERADMINs on **Administration → Operations & backups**.

A backup that has never passed the restore test is **not** considered verified.

Test evidence:

* `tests/operations/test_backups.py` runs the full round trip on SQLite locally and on PostgreSQL 16 in CI, using `pg_dump`, `pg_restore`, an exported snapshot and a scratch database.
* It also checks that damaged backups are rejected:
  * a corrupted archive;
  * wrong row counts;
  * a missing upload;
  * an altered audit row;
  * path traversal in the media archive.

## 1. What is backed up

| Data | Method | Where |
|---|---|---|
| PostgreSQL database | `pg_dump --format=custom` run as `portal_owner`. Grants are included, so the audit-table REVOKEs come back on restore. | `database.dump` |
| Private uploads (`PRIVATE_MEDIA_ROOT`) | `tar` + gzip of regular files only (symlinks are skipped) | `media.tar.gz` |
| Integrity data | SHA-256 and size of each file; row count of every table; head of each audit seal chain; applied migrations | `manifest.json` |
| Configuration | Kept in git (the repository); secrets in `/etc/school-portal` and the password safe. **Never in a backup.** | — |

The counts, seal heads, migration list and dump all come from **one exported snapshot**: a REPEATABLE READ transaction plus `pg_dump --snapshot`. The manifest therefore describes exactly what is in the dump, even while users keep writing.

Each backup is a directory `portal-<UTC timestamp>/` in `BACKUP_DIR`, created with mode 700 (files 600).

* It is written as `….partial` and renamed only when complete, so an interrupted run never becomes the latest backup.
* `BACKUP_KEEP` (default 14) complete backups are kept locally. Older backups and leftover partial directories are pruned after each successful run.

## 2. Schedule, retention and recovery objectives

| | Value |
|---|---|
| Backup | daily (`backup` job) |
| Restore test | weekly (`restore_test` job), plus before every release (`DEPLOYMENT.md` §7) |
| Local retention | 14 daily backups on the encrypted `backups` volume |
| Off-site retention | 7 daily, 4 weekly, 12 monthly. Enforce this with the object store's lifecycle rules on the `offsite_copy.sh` target, with object lock/immutability enabled. |
| RPO | **24 h** with nightly dumps. For a smaller RPO, add continuous WAL archiving (for example pgBackRest or WAL-G) for point-in-time recovery; this is not part of this repository. |
| RTO | about 1 h for a database of a few GB: provision, restore, verify (§5) |

## 3. Protection of backups

Backups contain personal data, so they are treated like the production database.

* **At rest, locally:** the Docker data directory (the `backups` volume) must be on an encrypted disk or volume.
* **Off site:** `deploy/scripts/offsite_copy.sh` runs on the host from cron. It:
  * tars the newest complete backup;
  * encrypts it with [age](https://age-encryption.org) to the backup operators' **public** keys;
  * uploads it with `rclone copy --immutable`, together with a SHA-256 file.
  
  The private key never lives on the production host: it is kept offline, in two-person custody.
* **Access:**
  * Only the `backup` container writes the volume; it runs as an unprivileged user on the internal network.
  * The portal can show backup and restore-test **status** (`manage_backups`), but it can never download a backup.
* **Logs:** backups and restore tests are recorded as `MaintenanceRun` rows. Command output never contains data values.

## 4. The restore test (`manage.py restore_test [backup-dir] [--keep-scratch]`)

The test uses the latest complete backup unless a directory is given. It runs these steps and fails (non-zero exit, a failed `MaintenanceRun`) on the first category of problem:

1. **Manifest:** readable, a known format, and taken from the same database engine.
2. **Checksums:** every file must match its SHA-256. A corrupted backup is **not** restored.
3. **Uploads:** extracted with tarfile's `data` filter, which rejects absolute paths, `..`, devices and links outside the target. The number of files must equal the manifest.
4. **Database:**
   * PostgreSQL: drop and create `<DB_NAME>_restore_test` as `portal_restore` (CREATEDB only), then `pg_restore --exit-on-error --single-transaction --no-owner --no-privileges`. The scratch name must end in `_restore_test` and differ from the live database; anything else is refused before any SQL runs.
   * SQLite (development): the copy is opened directly.
5. **Comparison on the restored database:**
   * applied migrations equal the manifest;
   * every table's row count equals the manifest;
   * the audit seal heads equal the manifest;
   * **every seal chain re-verifies.** This also proves that no audit row was altered between sealing and backup;
   * every `StoredFile` row's upload is present in the archive.
6. **Cleanup:** the scratch database is dropped, unless `--keep-scratch` is given for an investigation.

## 5. Disaster recovery procedure

Rehearse this at least once a year on a spare host, and record the time taken (the RTO).

1. **Provision.** Set up a host as in `DEPLOYMENT.md` §2, steps 1–3: same image tag as the backup, new secrets are fine except `MFA_ENCRYPTION_KEYS` and `PORTAL_HMAC_KEY`. Without the old MFA keys every user must re-enrol MFA; without the old HMAC key, recovery codes, device cookies and pending one-time codes stop working.
2. **Start only the database:**
   ```bash
   docker compose … up -d db
   ```
   The init script creates the roles, the empty database and `btree_gist`.
3. **Fetch and decrypt** the backup on an operator machine, then copy it to the host:
   ```bash
   age -d -i operator.key portal-<ts>.tar.age | tar -x
   ```
4. **Verify the backup before touching anything:**
   ```bash
   docker compose … run --rm -v /restore:/restore:ro backup python manage.py restore_test /restore/portal-<ts>
   ```
5. **Restore the database** as the owner. Skip the extension entries: the init script already created the extension as superuser.
   ```bash
   docker compose … run --rm -v /restore:/restore:ro --entrypoint sh backup -c '
     pg_restore -l /restore/portal-<ts>/database.dump | grep -v " EXTENSION " > /tmp/restore.list &&
     PGPASSWORD="$BACKUP_DB_PASSWORD" pg_restore --exit-on-error --single-transaction --no-owner \
       -h db -U "$BACKUP_DB_USER" -d school_portal -L /tmp/restore.list /restore/portal-<ts>/database.dump'
   ```
6. **Restore the uploads** into the `private_media` volume:
   ```bash
   docker compose … run --rm -v /restore:/restore:ro --user portal --entrypoint tar web \
     -xzf /restore/portal-<ts>/media.tar.gz -C /app/var/private-media
   ```
7. **Check** that `migrate --check` passes and `verify_audit_seals` reports no problems:
   ```bash
   docker compose … run --rm web python manage.py migrate --check
   docker compose … run --rm web python manage.py verify_audit_seals
   ```
8. **Start everything** with `docker compose … up -d`, then smoke test:
   * sign in with a test account;
   * open a request attachment;
   * check the Operations page.
9. **Record the incident:** what was lost (writes after the backup time, up to the RPO), and announce it.

## 6. Manual commands

| Task | Command |
|---|---|
| Backup now | `python manage.py backup_portal [--dest DIR] [--keep N]` (or `run_jobs --job backup`) |
| Verify the latest backup | `python manage.py restore_test` |
| Verify a specific backup and keep the scratch DB | `python manage.py restore_test /backups/portal-<ts> --keep-scratch` |
| Off-site copy | `AGE_RECIPIENTS_FILE=… OFFSITE_TARGET=… BACKUP_VOLUME_DIR=… deploy/scripts/offsite_copy.sh` |

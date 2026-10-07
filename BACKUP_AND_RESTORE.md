# Backup and Recovery Procedures

## 1. Backup Strategy
A complete backup consists of:
1. **Relational Database**: PostgreSQL SQL dump or SQLite binary snapshot.
2. **Media Storage**: Uploaded files (profile photos, ticket attachments).
3. **Configuration & Secrets**: Environment variables and encryption keys (stored securely in secret managers).

## 2. Automated Backup Execution

### PostgreSQL Backup:
```bash
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="/var/backups/school_portal"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "${BACKUP_DIR}"

pg_dump -U "${DB_USER}" -h "${DB_HOST}" -Fc "${DB_NAME}" > "${BACKUP_DIR}/db_${TIMESTAMP}.dump"
tar -czf "${BACKUP_DIR}/media_${TIMESTAMP}.tar.gz" -C /app media/

# Retain 14 days of backups
find "${BACKUP_DIR}" -name "*.dump" -mtime +14 -delete
find "${BACKUP_DIR}" -name "*.tar.gz" -mtime +14 -delete
```

## 3. Restoration Test Procedure
**A backup is not considered verified until tested in a staging restoration container.**

### Step-by-Step Restoration Verification:
1. Spin up an isolated target database container:
   ```bash
   docker run --name pg-restore-test -e POSTGRES_PASSWORD=test -d postgres:16
   ```
2. Restore database from dump:
   ```bash
   pg_restore -U postgres -h localhost -d restore_test_db db_snapshot.dump
   ```
3. Run schema verification script:
   ```bash
   python manage.py check --database default
   python manage.py test apps.academics.tests apps.hostels.tests
   ```
4. Extract media files and verify checksums:
   ```bash
   tar -xzf media_snapshot.tar.gz -C /restore_media/
   ```

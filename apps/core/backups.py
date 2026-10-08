"""Backups and the restore test (BACKUP_AND_RESTORE.md).

A backup is a directory ``portal-<UTC timestamp>/`` holding the database (``pg_dump`` custom format,
or a SQLite copy in development), the private uploads (``media.tar.gz``) and ``manifest.json``:
SHA-256 of every file, row counts per table, the head of each audit seal chain and the applied
migrations, all read from the same snapshot as the dump. It is written as ``<name>.partial`` and
renamed when complete, so an interrupted run never becomes "the latest backup".

``restore_check`` restores a backup into a scratch database and proves it is usable: checksums, row
counts, migrations, seal chains (which also detects altered audit rows) and the presence of every
stored upload in the archive. A backup that has never passed this check is not considered verified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess  # nosec B404 # noqa: S404
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import load_backend
from django.utils import timezone

from apps.core.seals import seal_heads, verify_all

FORMAT_VERSION = 1
PREFIX = "portal-"
PARTIAL = ".partial"
MANIFEST = "manifest.json"
MEDIA_ARCHIVE = "media.tar.gz"
PG_DUMP_FILE = "database.dump"
SQLITE_FILE = "database.sqlite3"
SCRATCH_SUFFIX = "_restore_test"
SOURCE_ALIAS = "backup_source"
RESTORE_ALIAS = "restore_check"
TOOL_TIMEOUT = 60 * 60


class BackupError(Exception):
    """A backup could not be taken or restored."""


@dataclass
class RestoreReport:
    backup: Path
    problems: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        head = f"Restore test of {self.backup.name}: {'OK' if self.ok else 'FAILED'}"
        lines = [head, *(f"  checked: {c}" for c in self.checks), *(f"  PROBLEM: {p}" for p in self.problems)]
        return "\n".join(lines)


# --- helpers --------------------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_postgres(settings_dict: dict) -> bool:
    return settings_dict["ENGINE"] == "django.db.backends.postgresql"


def tracked_models():
    return sorted(
        (m for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy),
        key=lambda m: m._meta.label,
    )


@contextmanager
def temporary_alias(alias: str, settings_dict: dict):
    """A short-lived connection usable with ``.using(alias)``.

    It is registered for this thread only and never added to ``settings.DATABASES``, so nothing that
    iterates the configured databases (migrate, flush, the test runner) ever sees it.
    """
    wrapper = load_backend(settings_dict["ENGINE"]).DatabaseWrapper(settings_dict, alias)
    setattr(connections._connections, alias, wrapper)
    try:
        yield alias
    finally:
        wrapper.close()
        delattr(connections._connections, alias)


def _row_counts(alias: str, labels) -> dict[str, int | None]:
    counts = {}
    for label in labels:
        try:
            counts[label] = apps.get_model(label)._base_manager.using(alias).count()
        except (LookupError, DatabaseError):
            counts[label] = None
    return counts


def _snapshot_state(alias: str) -> dict:
    recorder = MigrationRecorder(connections[alias])
    return {
        "row_counts": _row_counts(alias, [m._meta.label for m in tracked_models()]),
        "seal_heads": seal_heads(using=alias),
        "migrations": sorted(f"{app}.{name}" for app, name in recorder.applied_migrations()),
    }


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise BackupError(f"{name} was not found on PATH (install the PostgreSQL 16 client tools).")
    return path


def _pg_env(settings_dict: dict, password: str) -> dict:
    env = {**os.environ, "PGPASSWORD": password or "", "PGCONNECT_TIMEOUT": "15"}
    sslmode = settings_dict.get("OPTIONS", {}).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = sslmode
    return env


def _pg_target(settings_dict: dict, user: str, dbname: str) -> list[str]:
    args = ["--host", settings_dict.get("HOST") or "localhost", "--username", user, "--dbname", dbname]
    if settings_dict.get("PORT"):
        args += ["--port", str(settings_dict["PORT"])]
    return args


def _run(args: list[str], env: dict) -> None:
    """Run a PostgreSQL client tool: fixed argument list, no shell, binary resolved by shutil.which."""
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False)  # noqa: S603 # nosec B603
    if result.returncode != 0:
        tool = Path(args[0]).name
        raise BackupError(f"{tool} failed (exit {result.returncode}): {result.stderr.strip()[-2000:]}")


def _live_settings() -> dict:
    return copy.deepcopy(connections["default"].settings_dict)


# --- backup ---------------------------------------------------------------------------------------


def _dump_postgres(work: Path) -> dict:
    sd = _live_settings()
    sd["USER"] = settings.BACKUP_DB_USER or sd["USER"]
    sd["PASSWORD"] = settings.BACKUP_DB_PASSWORD or sd["PASSWORD"]
    sd["CONN_MAX_AGE"] = 0
    with temporary_alias(SOURCE_ALIAS, sd), transaction.atomic(using=SOURCE_ALIAS):
        with connections[SOURCE_ALIAS].cursor() as cursor:
            # One snapshot for the counts, seal heads and pg_dump, so the manifest matches the dump exactly.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
        state = _snapshot_state(SOURCE_ALIAS)
        # Privileges are kept so a disaster restore re-applies the audit-table REVOKEs (layer 3).
        _run([_tool("pg_dump"), "--format=custom", "--no-owner", f"--snapshot={snapshot}",
              "--file", str(work / PG_DUMP_FILE), *_pg_target(sd, sd["USER"], sd["NAME"])],
             env=_pg_env(sd, sd["PASSWORD"]))
    return state


def _dump_sqlite(work: Path) -> dict:
    target = work / SQLITE_FILE
    source = connections["default"]
    source.ensure_connection()
    copy_conn = sqlite3.connect(target)
    try:
        # Development/test only. iterdump reads through Django's own connection (so it works inside an
        # open transaction) and emits triggers after the data, so append-only guards do not fire.
        copy_conn.executescript("\n".join(source.connection.iterdump()))
    finally:
        copy_conn.close()
    sd = _live_settings()
    sd["NAME"] = str(target)
    with temporary_alias(SOURCE_ALIAS, sd):
        return _snapshot_state(SOURCE_ALIAS)


def _archive_media(work: Path) -> int:
    root = Path(settings.MEDIA_ROOT)
    count = 0
    with tarfile.open(work / MEDIA_ARCHIVE, "w:gz") as tar:
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
                    count += 1
    return count


def complete_backups(dest: Path) -> list[Path]:
    if not dest.is_dir():
        return []
    return sorted(
        p for p in dest.iterdir()
        if p.is_dir() and p.name.startswith(PREFIX) and not p.name.endswith(PARTIAL) and (p / MANIFEST).is_file()
    )


def latest_backup(dest: Path | None = None) -> Path:
    backups = complete_backups(Path(dest or settings.BACKUP_DIR))
    if not backups:
        raise BackupError("No complete backup found.")
    return backups[-1]


def prune(dest: Path, keep: int) -> list[Path]:
    """Keep the newest ``keep`` complete backups; also remove leftovers of interrupted runs."""
    removed = complete_backups(dest)[:-keep] if keep > 0 else []
    removed += [p for p in dest.iterdir() if p.is_dir() and p.name.startswith(PREFIX) and p.name.endswith(PARTIAL)]
    for path in removed:
        shutil.rmtree(path)
    return removed


def create_backup(dest: Path | None = None, *, keep: int | None = None) -> Path:
    dest = Path(dest or settings.BACKUP_DIR)
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = timezone.now()
    final = dest / f"{PREFIX}{now:%Y%m%dT%H%M%SZ}"
    if final.exists():
        raise BackupError(f"{final.name} already exists.")
    work = dest / (final.name + PARTIAL)
    postgres = _is_postgres(connections["default"].settings_dict)
    old_umask = os.umask(0o077)  # backups contain personal data: owner-only files
    try:
        work.mkdir(mode=0o700)
        state = _dump_postgres(work) if postgres else _dump_sqlite(work)
        media_files = _archive_media(work)
        files = {p.name: {"sha256": sha256_of(p), "bytes": p.stat().st_size} for p in sorted(work.iterdir())}
        manifest = {
            "format": FORMAT_VERSION,
            "created_at": now.isoformat(),
            "engine": "postgresql" if postgres else "sqlite",
            "database_file": PG_DUMP_FILE if postgres else SQLITE_FILE,
            "files": files,
            "media_files": media_files,
            **state,
        }
        (work / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        work.rename(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        os.umask(old_umask)
    if keep:
        prune(dest, keep)
    return final


# --- restore test ---------------------------------------------------------------------------------


def scratch_name(live: str) -> str:
    """The scratch database; refuses anything that could be the live database."""
    name = settings.RESTORE_SCRATCH_DB or f"{live}{SCRATCH_SUFFIX}"
    if name == live or not name.endswith(SCRATCH_SUFFIX):
        raise BackupError(f"Refusing scratch database {name!r}: it must end in {SCRATCH_SUFFIX} and differ "
                          "from the live database.")
    return name


def _admin_connection(sd: dict, user: str, password: str):
    import psycopg

    return psycopg.connect(host=sd.get("HOST") or "localhost", port=sd.get("PORT") or None, user=user,
                           password=password, dbname="postgres", autocommit=True,
                           sslmode=sd.get("OPTIONS", {}).get("sslmode", "prefer"), connect_timeout=15)


def _admin_execute(sd: dict, user: str, password: str, statement: str, name: str) -> None:
    """Run CREATE/DROP DATABASE on the maintenance database (the name is quoted as an identifier)."""
    import psycopg
    from psycopg import sql

    try:
        with _admin_connection(sd, user, password) as conn:
            conn.execute(sql.SQL(statement).format(sql.Identifier(name)))
    except psycopg.Error as exc:
        raise BackupError(f"{statement.split(' {}')[0]} failed: {exc}") from exc


def _drop_database(sd: dict, user: str, password: str, name: str) -> None:
    _admin_execute(sd, user, password, "DROP DATABASE IF EXISTS {} WITH (FORCE)", name)


@contextmanager
def _restored_database(backup: Path, manifest: dict, tmp: Path, keep_scratch: bool):
    sd = _live_settings()
    sd["CONN_MAX_AGE"] = 0
    if manifest["engine"] == "sqlite":
        target = tmp / "restore.sqlite3"
        shutil.copyfile(backup / SQLITE_FILE, target)
        sd["NAME"] = str(target)
        with temporary_alias(RESTORE_ALIAS, sd):
            yield RESTORE_ALIAS
        return

    scratch = scratch_name(sd["NAME"])
    user = settings.RESTORE_DB_USER or sd["USER"]
    password = settings.RESTORE_DB_PASSWORD or sd["PASSWORD"]
    _drop_database(sd, user, password, scratch)
    _admin_execute(sd, user, password, "CREATE DATABASE {}", scratch)
    try:
        _run([_tool("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
              *_pg_target(sd, user, scratch), str(backup / PG_DUMP_FILE)], env=_pg_env(sd, password))
        sd.update(NAME=scratch, USER=user, PASSWORD=password)
        with temporary_alias(RESTORE_ALIAS, sd):
            yield RESTORE_ALIAS
    finally:
        if not keep_scratch:
            _drop_database(sd, user, password, scratch)


def _extract_media(archive: Path, target: Path) -> int:
    target.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(target, filter="data")  # rejects absolute paths, traversal, links outside, devices
    return sum(1 for p in target.rglob("*") if p.is_file())


def _compare(alias: str, manifest: dict, media_root: Path, report: RestoreReport) -> None:
    restored_migrations = sorted(f"{a}.{n}" for a, n in MigrationRecorder(connections[alias]).applied_migrations())
    if restored_migrations != manifest["migrations"]:
        report.problems.append("applied migrations differ from the manifest")
    else:
        report.checks.append(f"{len(restored_migrations)} applied migrations")

    expected = manifest["row_counts"]
    actual = _row_counts(alias, expected)
    mismatches = [f"{label}: expected {n} rows, restored {actual[label]}" for label, n in expected.items()
                  if actual[label] != n]
    report.problems.extend(mismatches)
    if not mismatches:
        report.checks.append(f"row counts of {len(expected)} tables ({sum(expected.values())} rows)")

    if seal_heads(using=alias) != manifest["seal_heads"]:
        report.problems.append("audit seal heads differ from the manifest")
    seal_problems = verify_all(using=alias)
    report.problems.extend(f"audit seal: {p}" for p in seal_problems)
    if not seal_problems:
        report.checks.append("audit seal chains verified")

    from apps.core.models import StoredFile

    missing = [name for name in StoredFile.objects.using(alias).values_list("storage_name", flat=True)
               if not (media_root / name).is_file()]
    report.problems.extend(f"stored file missing from the media archive: {name}" for name in missing[:20])
    if not missing:
        report.checks.append("every stored file present in the media archive")


def restore_check(backup: Path, *, keep_scratch: bool = False) -> RestoreReport:
    backup = Path(backup)
    report = RestoreReport(backup)
    try:
        manifest = json.loads((backup / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.problems.append(f"manifest unreadable: {exc}")
        return report
    if manifest.get("format") != FORMAT_VERSION:
        report.problems.append(f"unsupported manifest format {manifest.get('format')!r}")
        return report
    current_engine = "postgresql" if _is_postgres(connections["default"].settings_dict) else "sqlite"
    if manifest.get("engine") != current_engine:
        report.problems.append(f"backup engine {manifest.get('engine')} cannot be restored on {current_engine}")
        return report

    for name, meta in manifest["files"].items():
        path = backup / name
        if not path.is_file():
            report.problems.append(f"{name} is missing")
        elif sha256_of(path) != meta["sha256"]:
            report.problems.append(f"{name} checksum mismatch (corrupted or altered)")
    if report.problems:
        return report  # never restore data that failed its checksum
    report.checks.append(f"checksums of {len(manifest['files'])} files")

    with tempfile.TemporaryDirectory(prefix="portal-restore-") as tmp:
        tmp_path = Path(tmp)
        try:
            extracted = _extract_media(backup / MEDIA_ARCHIVE, tmp_path / "media")
        except (tarfile.TarError, OSError) as exc:
            report.problems.append(f"media archive could not be extracted: {exc}")
            return report
        if extracted != manifest["media_files"]:
            report.problems.append(f"media archive holds {extracted} files, manifest says {manifest['media_files']}")
        try:
            with _restored_database(backup, manifest, tmp_path, keep_scratch) as alias:
                _compare(alias, manifest, tmp_path / "media", report)
        except (BackupError, DatabaseError, OSError) as exc:
            report.problems.append(f"database restore failed: {exc}")
    return report

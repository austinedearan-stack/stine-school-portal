"""Backups and the restore test (BACKUP_AND_RESTORE.md; ARCHITECTURE.md §9 phase 15).

The round trip runs on SQLite everywhere and on PostgreSQL (pg_dump/pg_restore, snapshot export,
scratch database) in CI. Tampering tests prove the restore test fails loudly instead of passing a
damaged backup.
"""

import io
import json
import shutil
import sqlite3
import tarfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.utils import timezone
from PIL import Image

from apps.core import backups, files
from apps.core.audit import record_audit_event
from apps.core.models import FilePurpose
from apps.core.seals import seal_all
from tests import factories as f

# SQLite: the dump reads through the test connection, so the normal test transaction suffices.
# PostgreSQL: pg_dump is a separate session and only sees committed data, so tests must commit.
POSTGRES = settings.DATABASES["default"]["ENGINE"].endswith("postgresql")
pytestmark = pytest.mark.django_db(transaction=POSTGRES)


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def dest(settings, tmp_path):
    settings.BACKUP_DIR = tmp_path / "backups"
    return settings.BACKUP_DIR


def _populate():
    student = f.student()
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    upload = SimpleUploadedFile("photo.png", buffer.getvalue(), content_type="image/png")
    stored = files.store(files.validate_upload(upload, FilePurpose.REQUEST_ATTACHMENT), owner=student.user,
                         purpose=FilePurpose.REQUEST_ATTACHMENT)
    record_audit_event(None, "TEST.BACKUP")
    with mock.patch("apps.core.seals.timezone.now", return_value=timezone.now() + timedelta(hours=1)):
        assert seal_all()
    return stored


def _rewrite_checksum(backup: Path, name: str):
    manifest = json.loads((backup / backups.MANIFEST).read_text())
    manifest["files"][name]["sha256"] = backups.sha256_of(backup / name)
    (backup / backups.MANIFEST).write_text(json.dumps(manifest))


def test_round_trip_backup_restores_and_verifies(media, dest):
    _populate()
    backup = backups.create_backup()
    manifest = json.loads((backup / backups.MANIFEST).read_text())
    assert manifest["engine"] in ("sqlite", "postgresql")
    assert manifest["row_counts"]["accounts.User"] >= 1
    assert manifest["row_counts"]["core.StoredFile"] == 1
    assert manifest["media_files"] == 1
    assert manifest["seal_heads"]["AUDIT"][0] > 0
    assert set(manifest["files"]) == {manifest["database_file"], backups.MEDIA_ARCHIVE}

    report = backups.restore_check(backup)
    assert report.ok, report.summary()
    assert any("audit seal chains verified" in c for c in report.checks)
    assert any("stored file present" in c for c in report.checks)


def test_corrupted_archive_fails_the_checksum_and_is_not_restored(media, dest):
    _populate()
    backup = backups.create_backup()
    with (backup / backups.MEDIA_ARCHIVE).open("ab") as fh:
        fh.write(b"tampered")
    report = backups.restore_check(backup)
    assert not report.ok and "media.tar.gz checksum mismatch" in report.problems[0]
    assert report.checks == []  # nothing was restored


def test_row_count_mismatch_is_reported(media, dest):
    _populate()
    backup = backups.create_backup()
    manifest = json.loads((backup / backups.MANIFEST).read_text())
    manifest["row_counts"]["accounts.User"] += 1
    (backup / backups.MANIFEST).write_text(json.dumps(manifest))
    report = backups.restore_check(backup)
    assert any(p.startswith("accounts.User: expected") for p in report.problems)


def test_upload_missing_from_archive_is_reported(media, dest):
    stored = _populate()
    (Path(media) / stored.storage_name).unlink()
    backup = backups.create_backup()
    manifest = json.loads((backup / backups.MANIFEST).read_text())
    assert manifest["media_files"] == 0
    report = backups.restore_check(backup)
    assert any("stored file missing" in p for p in report.problems)


@pytest.mark.skipif("not __import__('django').conf.settings.DATABASES['default']['ENGINE'].endswith('sqlite3')")
def test_altered_audit_row_in_backup_is_detected_by_the_seal_chain(media, dest):
    _populate()
    backup = backups.create_backup()
    db = backup / backups.SQLITE_FILE
    conn = sqlite3.connect(db)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        conn.execute(f'DROP TRIGGER "{name}"')
    conn.execute("UPDATE core_auditlog SET action = 'TEST.FORGED' WHERE action = 'TEST.BACKUP'")
    conn.commit()
    conn.close()
    _rewrite_checksum(backup, backups.SQLITE_FILE)  # an attacker who can also edit the manifest
    report = backups.restore_check(backup)
    assert any("hash mismatch" in p for p in report.problems), report.summary()


def test_media_archive_with_path_traversal_is_rejected(media, dest):
    _populate()
    backup = backups.create_backup()
    archive = backup / backups.MEDIA_ARCHIVE
    with tarfile.open(archive, "w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("../../escape.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    _rewrite_checksum(backup, backups.MEDIA_ARCHIVE)
    report = backups.restore_check(backup)
    assert any("could not be extracted" in p for p in report.problems)
    assert not (Path(archive).parent.parent / "escape.txt").exists()


def test_engine_mismatch_and_unknown_format_are_refused(media, dest):
    _populate()
    backup = backups.create_backup()
    manifest = json.loads((backup / backups.MANIFEST).read_text())
    manifest["engine"] = "oracle"
    (backup / backups.MANIFEST).write_text(json.dumps(manifest))
    assert "cannot be restored" in backups.restore_check(backup).problems[0]
    manifest["format"] = 99
    (backup / backups.MANIFEST).write_text(json.dumps(manifest))
    assert "unsupported manifest format" in backups.restore_check(backup).problems[0]
    (backup / backups.MANIFEST).write_text("{not json")
    assert "manifest unreadable" in backups.restore_check(backup).problems[0]


def test_backup_files_are_private_and_partial_runs_never_count(media, dest):
    _populate()
    with mock.patch("apps.core.backups._archive_media", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            backups.create_backup()
    assert list(Path(dest).iterdir()) == []  # the .partial directory was removed
    with pytest.raises(backups.BackupError):
        backups.latest_backup()
    backup = backups.create_backup()
    assert backups.latest_backup() == backup
    (Path(dest) / "portal-20990101T000000Z.partial").mkdir()
    assert backups.latest_backup() == backup


def test_prune_keeps_the_newest_backups(dest):
    root = Path(dest)
    root.mkdir(parents=True)
    names = [f"portal-2026010{i}T000000Z" for i in range(1, 6)]
    for name in names:
        (root / name).mkdir()
        (root / name / backups.MANIFEST).write_text("{}")
    (root / "portal-20260109T000000Z.partial").mkdir()
    (root / "unrelated").mkdir()
    removed = backups.prune(root, keep=2)
    assert len(removed) == 4
    assert sorted(p.name for p in root.iterdir()) == [*names[-2:], "unrelated"]


def test_scratch_database_name_can_never_be_the_live_database(settings):
    assert backups.scratch_name("school_portal") == "school_portal_restore_test"
    for bad in ("school_portal", "school_portal_copy"):
        settings.RESTORE_SCRATCH_DB = bad
        with pytest.raises(backups.BackupError, match="Refusing"):
            backups.scratch_name("school_portal")


def test_commands(media, dest, capsys):
    _populate()
    call_command("backup_portal", "--keep", "3")
    assert "Backup written: portal-" in capsys.readouterr().out
    call_command("restore_test")
    assert ": OK" in capsys.readouterr().out
    latest = backups.latest_backup()
    with (latest / backups.MEDIA_ARCHIVE).open("ab") as fh:
        fh.write(b"x")
    with pytest.raises(CommandError, match="Restore test failed"):
        call_command("restore_test", str(latest))
    shutil.rmtree(dest)
    with pytest.raises(CommandError, match="No complete backup"):
        call_command("restore_test")


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_postgres_round_trip_uses_pg_dump_snapshot_and_scratch_database(media, dest):
    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        pytest.skip("PostgreSQL client tools not installed")
    _populate()
    backup = backups.create_backup()
    assert (backup / backups.PG_DUMP_FILE).stat().st_size > 0
    report = backups.restore_check(backup)
    assert report.ok, report.summary()

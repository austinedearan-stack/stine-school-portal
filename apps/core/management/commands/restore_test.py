"""Restore a backup into a scratch database and verify it (BACKUP_AND_RESTORE.md "Restore test")."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.backups import BackupError, latest_backup, restore_check


class Command(BaseCommand):
    help = "Restore the latest (or given) backup into a scratch database and verify it; non-zero exit on failure."

    def add_arguments(self, parser):
        parser.add_argument("backup", nargs="?", type=Path, help="Backup directory (default: latest in BACKUP_DIR).")
        parser.add_argument("--keep-scratch", action="store_true", help="Leave the scratch database for inspection.")

    def handle(self, *args, **options):
        try:
            backup = options["backup"] or latest_backup()
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        report = restore_check(backup, keep_scratch=options["keep_scratch"])
        self.stdout.write(report.summary())
        if not report.ok:
            raise CommandError(f"Restore test failed with {len(report.problems)} problem(s).")

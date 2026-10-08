"""Take a backup (database + private uploads + manifest) into BACKUP_DIR; see BACKUP_AND_RESTORE.md."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.backups import BackupError, create_backup


class Command(BaseCommand):
    help = "Create a verified-format backup and prune old ones."

    def add_arguments(self, parser):
        parser.add_argument("--dest", type=Path, default=None, help="Backup directory (default BACKUP_DIR).")
        parser.add_argument("--keep", type=int, default=None, help="Backups to keep (default BACKUP_KEEP).")

    def handle(self, *args, **options):
        keep = options["keep"] if options["keep"] is not None else settings.BACKUP_KEEP
        try:
            path = create_backup(options["dest"], keep=keep)
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Backup written: {path.name}")

"""Scheduled job (e.g. every 15 minutes): seal settled rows of the append-only streams."""

from django.core.management.base import BaseCommand

from apps.core.seals import seal_all


class Command(BaseCommand):
    help = "Hash-chain new audit, security and request-history rows into AuditSeal records."

    def handle(self, *args, **options):
        seals = seal_all()
        for seal in seals:
            self.stdout.write(f"{seal.stream}: sealed {seal.row_count} rows ({seal.from_seq}-{seal.to_seq}) {seal.sha256}")
        if not seals:
            self.stdout.write("Nothing to seal.")

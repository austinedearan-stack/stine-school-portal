"""Scheduled job: close RESOLVED/APPROVED/REJECTED requests idle for REQUEST_AUTO_CLOSE_DAYS
(approved transfers that have not been executed are never closed automatically)."""

from django.core.management.base import BaseCommand

from apps.student_requests.services import close_stale


class Command(BaseCommand):
    help = "Close finished requests after the configured number of idle days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None)

    def handle(self, *args, **options):
        self.stdout.write(f"Closed {close_stale(options['days'])} request(s).")

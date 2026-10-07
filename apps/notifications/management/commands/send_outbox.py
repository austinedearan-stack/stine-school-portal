"""Scheduled job: send queued notification emails (only when NOTIFICATION_EMAIL_KINDS is configured)."""

from django.core.management.base import BaseCommand

from apps.notifications.services import send_outbox


class Command(BaseCommand):
    help = "Send pending notification emails from the outbox."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        sent, failed = send_outbox(options["limit"])
        self.stdout.write(f"Sent {sent}, failed {failed}.")

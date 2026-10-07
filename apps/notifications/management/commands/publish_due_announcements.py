"""Scheduled job: notify the audience of announcements whose publish time has arrived."""

from django.core.management.base import BaseCommand

from apps.notifications.services import deliver_due


class Command(BaseCommand):
    help = "Deliver notifications for published announcements that reached their publish time."

    def handle(self, *args, **options):
        self.stdout.write(f"Delivered {deliver_due()} notification(s).")

"""Periodic job: withdraw hostel offers whose acceptance deadline has passed (also done lazily in-request)."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.hostels.services import expire_stale_offers


class Command(BaseCommand):
    help = "Mark expired hostel offers EXPIRED and return their applications to APPROVED."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_stale_offers()} offer(s).")

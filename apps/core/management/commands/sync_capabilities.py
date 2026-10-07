"""Re-apply the capability catalog: create missing permissions and reset default groups' permissions."""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.capabilities import default_groups, sync_capability_groups


class Command(BaseCommand):
    help = "Create/refresh the capability permissions and the default capability groups."

    @transaction.atomic
    def handle(self, *args, **options):
        sync_capability_groups(Group, Permission, ContentType)
        self.stdout.write(self.style.SUCCESS(f"Synchronised {len(default_groups())} capability groups."))

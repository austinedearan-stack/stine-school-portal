"""Re-encrypt every stored MFA secret with the current primary key in MFA_ENCRYPTION_KEYS.

Rotation: prepend the new key to MFA_ENCRYPTION_KEYS (keep the old one after it), deploy, run this
command, then remove the old key in a later deploy.
"""

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import MFADevice
from apps.core.audit import record_audit_event


class Command(BaseCommand):
    help = "Re-encrypt MFA secrets with the first key of MFA_ENCRYPTION_KEYS."

    @transaction.atomic
    def handle(self, *args, **options):
        fernet = MultiFernet([Fernet(k.encode()) for k in settings.MFA_ENCRYPTION_KEYS])
        count = 0
        for device in MFADevice.objects.select_for_update().iterator():
            device.secret_encrypted = fernet.rotate(device.secret_encrypted.encode()).decode()
            device.save(update_fields=["secret_encrypted"])
            count += 1
        record_audit_event(None, "SYSTEM.MFA_KEYS_ROTATED", object_type="MFADevice", changes={"devices": count})
        self.stdout.write(self.style.SUCCESS(f"Re-encrypted {count} MFA secret(s)."))

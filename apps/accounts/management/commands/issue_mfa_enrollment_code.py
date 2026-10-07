"""Issue a one-time MFA enrollment code for an account that has no authenticator (operator use).

For routine use, administrators issue codes from the admin panel (audited with their identity).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import mfa
from apps.accounts.models import User
from apps.core.audit import record_audit_event


class Command(BaseCommand):
    help = "Print a new one-time MFA enrollment code for USERNAME (invalidates older unused codes)."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        user = User.objects.filter(username__iexact=options["username"], is_active=True).first()
        if user is None:
            raise CommandError("No active account with that username.")
        if mfa.confirmed_device(user):
            raise CommandError("This account already has an authenticator. Reset MFA from the admin panel instead.")
        code = mfa.issue_enrollment_code(user, issued_by=None)
        record_audit_event(None, "ACCOUNT.MFA_ENROLLMENT_CODE_ISSUED", user, changes={"via": "management command"})
        self.stdout.write(f"Enrollment code for {user.username}: {code}")

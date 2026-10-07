"""Create a SUPERADMIN account (never via ``createsuperuser``, which would set is_superuser - D4).

The account gets the Superadmin capability group, a random password and a one-time MFA enrollment
code. Both are printed ONCE and must be handed to the person out of band; nothing is written to disk.
At first login the person must enrol an authenticator app using the enrollment code.
"""

import getpass

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts import mfa
from apps.accounts.models import User
from apps.accounts.passwords import generate_initial_password
from apps.core.audit import record_audit_event
from apps.core.capabilities import SUPERADMIN_GROUP, Role


class Command(BaseCommand):
    help = "Create a portal superadmin with a random password and a one-time MFA enrollment code."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--prompt-password", action="store_true", help="Type the password instead of generating one.")

    def handle(self, *args, **options):
        if User.objects.filter(username__iexact=options["username"]).exists():
            raise CommandError("That username already exists.")
        password = generate_initial_password()
        if options["prompt_password"]:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        with transaction.atomic():
            user = User.objects.create_user(
                username=options["username"], email=options["email"], password=password, role=Role.SUPERADMIN,
                first_name=options["first_name"], last_name=options["last_name"],
            )
            user.groups.add(Group.objects.get(name=SUPERADMIN_GROUP))
            code = mfa.issue_enrollment_code(user, issued_by=None)
            record_audit_event(None, "ACCOUNT.SUPERADMIN_CREATED", user, changes={"via": "management command"})
        self.stdout.write(self.style.SUCCESS(f"Superadmin {user.username} created."))
        if not options["prompt_password"]:
            self.stdout.write(f"  Initial password : {password}")
        self.stdout.write(f"  Enrollment code  : {code}  (valid {int(mfa.ENROLLMENT_CODE_TTL.total_seconds() // 3600)} h)")
        self.stdout.write("Deliver these out of band. They are not stored anywhere in readable form.")

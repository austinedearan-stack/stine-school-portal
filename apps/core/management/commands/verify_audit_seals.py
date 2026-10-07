"""Verify the audit seal chains; exits non-zero (for monitoring) if anything was altered."""

import sys

from django.core.management.base import BaseCommand

from apps.core.seals import verify_all


class Command(BaseCommand):
    help = "Recompute every audit seal and report tampering."

    def handle(self, *args, **options):
        problems = verify_all()
        for problem in problems:
            self.stderr.write(problem)
        if problems:
            sys.exit(1)
        self.stdout.write("All audit seals verified.")

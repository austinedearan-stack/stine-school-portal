"""Verify the audit seal chains; exits non-zero (for monitoring) if anything was altered."""

from django.core.management.base import BaseCommand, CommandError

from apps.core.seals import verify_all


class Command(BaseCommand):
    help = "Recompute every audit seal and report tampering."

    def handle(self, *args, **options):
        problems = verify_all()
        for problem in problems:
            self.stderr.write(problem)
        if problems:
            raise CommandError(f"{len(problems)} audit seal problem(s) found.")
        self.stdout.write("All audit seals verified.")

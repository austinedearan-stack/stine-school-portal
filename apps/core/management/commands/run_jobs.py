"""Run scheduled jobs: a long-running scheduler loop, one pass (--once) or a single job now (--job)."""

import signal
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.core import jobs


class Command(BaseCommand):
    help = "Run the portal's scheduled jobs (see DEPLOYMENT.md 'Scheduled jobs')."

    def add_arguments(self, parser):
        parser.add_argument("--group", choices=[*jobs.GROUPS, "all"], default=jobs.MAINTENANCE)
        parser.add_argument("--once", action="store_true", help="Run every due job once and exit.")
        parser.add_argument("--job", help="Run this job now, whether due or not, and exit.")
        parser.add_argument("--interval", type=int, default=30, help="Seconds between scheduler passes.")

    def handle(self, *args, **options):
        if options["job"]:
            job = jobs.JOBS_BY_NAME.get(options["job"])
            if job is None:
                raise CommandError(f"Unknown job {options['job']!r}. Known: {', '.join(jobs.JOBS_BY_NAME)}")
            return self._report([jobs.run_job(job)])
        if options["once"]:
            return self._report(jobs.run_due(options["group"]))

        stopping = []
        signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
        signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
        self.stdout.write(f"Scheduler started for group {options['group']!r}.")
        while not stopping:
            close_old_connections()
            for run in jobs.run_due(options["group"]):
                self.stdout.write(f"{run.job}: {'ok' if run.ok else 'FAILED'}")
            for _ in range(max(1, options["interval"])):
                if stopping:
                    break
                time.sleep(1)
        self.stdout.write("Scheduler stopped.")
        return None

    def _report(self, runs):
        for run in runs:
            self.stdout.write(f"{run.job}: {'ok' if run.ok else 'FAILED'}")
        failed = [run.job for run in runs if not run.ok]
        if failed:
            raise CommandError(f"Failed: {', '.join(failed)}")

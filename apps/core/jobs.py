"""Scheduled jobs (DEPLOYMENT.md "Scheduled jobs").

One ``run_jobs`` process per group runs in production: ``maintenance`` (runtime database role) and
``backup`` (the ops image with the PostgreSQL client and the backup credentials). Each run is recorded
as a ``MaintenanceRun`` so SUPERADMINs can see on the Operations page whether backups, restore tests
and seal verification are actually happening. Due-ness is computed from the last recorded run, so a
restart neither skips nor repeats jobs; a failed job is retried after ``RETRY_AFTER``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from apps.core.models import MaintenanceRun

logger = logging.getLogger("portal.jobs")

MAINTENANCE, BACKUP = "maintenance", "backup"
GROUPS = (MAINTENANCE, BACKUP)
RETRY_AFTER = timedelta(hours=1)
SUMMARY_LIMIT = 4000


@dataclass(frozen=True)
class Job:
    name: str
    command: str
    every: timedelta
    group: str = MAINTENANCE
    args: tuple = ()


JOBS = (
    Job("publish_due_announcements", "publish_due_announcements", timedelta(minutes=1)),
    Job("send_outbox", "send_outbox", timedelta(minutes=1)),
    Job("expire_hostel_offers", "expire_hostel_offers", timedelta(minutes=5)),
    Job("seal_audit_log", "seal_audit_log", timedelta(minutes=15)),
    Job("verify_audit_seals", "verify_audit_seals", timedelta(days=1)),
    Job("close_stale_requests", "close_stale_requests", timedelta(days=1)),
    Job("clearsessions", "clearsessions", timedelta(days=1)),
    Job("backup", "backup_portal", timedelta(days=1), BACKUP),
    Job("restore_test", "restore_test", timedelta(days=7), BACKUP),
)
JOBS_BY_NAME = {job.name: job for job in JOBS}


def jobs_in(group: str) -> list[Job]:
    return [job for job in JOBS if group == "all" or job.group == group]


def last_run(job: Job) -> MaintenanceRun | None:
    return MaintenanceRun.objects.filter(job=job.name).order_by("-started_at").first()


def is_due(job: Job, now=None) -> bool:
    now = now or timezone.now()
    last = last_run(job)
    if last is None:
        return True
    wait = job.every if last.ok else min(job.every, RETRY_AFTER)
    return now >= last.started_at + wait


def run_job(job: Job) -> MaintenanceRun:
    output = StringIO()
    started = timezone.now()
    ok = True
    try:
        call_command(job.command, *job.args, stdout=output, stderr=output)
    except Exception as exc:  # a failing job must not stop the scheduler; it is recorded and logged
        ok = False
        output.write(f"\n{type(exc).__name__}: {exc}")
        logger.exception("job_failed", extra={"job": job.name})
    summary = output.getvalue().strip()
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[:SUMMARY_LIMIT] + "\n[truncated]"
    run = MaintenanceRun.objects.create(job=job.name, started_at=started, finished_at=timezone.now(), ok=ok,
                                        summary=summary)
    logger.info("job_finished", extra={"job": job.name, "ok": ok,
                                        "seconds": round((run.finished_at - started).total_seconds(), 2)})
    return run


def run_due(group: str, now=None) -> list[MaintenanceRun]:
    return [run_job(job) for job in jobs_in(group) if is_due(job, now)]


def status(group: str = "all", now=None) -> list[dict]:
    """Per job: schedule, last run and whether it is overdue (2x its interval without a successful run)."""
    now = now or timezone.now()
    rows = []
    for job in jobs_in(group):
        last = last_run(job)
        last_ok = MaintenanceRun.objects.filter(job=job.name, ok=True).order_by("-started_at").first()
        overdue = last_ok is None or now - last_ok.started_at > 2 * job.every
        rows.append({"job": job, "last": last, "last_ok": last_ok, "overdue": overdue})
    return rows

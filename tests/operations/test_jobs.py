"""Scheduled jobs and the Operations page (DEPLOYMENT.md "Scheduled jobs")."""

import signal
from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from apps.core import jobs
from apps.core.capabilities import Role
from apps.core.models import MaintenanceRun
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


def test_first_pass_runs_every_maintenance_job_then_nothing_is_due():
    runs = jobs.run_due(jobs.MAINTENANCE)
    assert {r.job for r in runs} == {j.name for j in jobs.jobs_in(jobs.MAINTENANCE)}
    assert all(r.ok for r in runs), [(r.job, r.summary) for r in runs if not r.ok]
    assert jobs.run_due(jobs.MAINTENANCE) == []
    assert "backup" not in {r.job for r in runs}  # backups run in their own (ops) container


def test_jobs_become_due_after_their_interval_and_failures_retry_sooner():
    job = jobs.JOBS_BY_NAME["verify_audit_seals"]
    with mock.patch("apps.core.management.commands.verify_audit_seals.verify_all", return_value=["AUDIT: broken"]):
        run = jobs.run_job(job)
    assert not run.ok and "audit seal problem" in run.summary
    now = timezone.now()
    assert not jobs.is_due(job, now + timedelta(minutes=30))
    assert jobs.is_due(job, now + jobs.RETRY_AFTER + timedelta(seconds=1))  # retried before a day passes
    ok_run = jobs.run_job(job)
    assert ok_run.ok
    assert not jobs.is_due(job, now + timedelta(hours=23))
    assert jobs.is_due(job, now + timedelta(days=1, minutes=1))


def test_status_flags_overdue_jobs():
    jobs.run_job(jobs.JOBS_BY_NAME["seal_audit_log"])
    rows = {row["job"].name: row for row in jobs.status()}
    assert rows["seal_audit_log"]["overdue"] is False
    assert rows["backup"]["overdue"] is True  # never succeeded
    later = timezone.now() + timedelta(hours=1)
    assert {row["job"].name: row for row in jobs.status(now=later)}["seal_audit_log"]["overdue"] is True


def test_long_output_is_truncated():
    job = jobs.Job("noisy", "verify_audit_seals", timedelta(days=1))
    with mock.patch("apps.core.management.commands.verify_audit_seals.verify_all", return_value=["x" * 9000]):
        run = jobs.run_job(job)
    assert len(run.summary) <= jobs.SUMMARY_LIMIT + 20 and run.summary.endswith("[truncated]")


def test_run_jobs_command(capsys):
    call_command("run_jobs", "--job", "seal_audit_log")
    assert "seal_audit_log: ok" in capsys.readouterr().out
    with pytest.raises(CommandError, match="Unknown job"):
        call_command("run_jobs", "--job", "rm -rf")
    call_command("run_jobs", "--once")
    assert MaintenanceRun.objects.filter(job="send_outbox").exists()
    with mock.patch("apps.core.management.commands.verify_audit_seals.verify_all", return_value=["AUDIT: broken"]):
        with pytest.raises(CommandError, match="Failed: verify_audit_seals"):
            call_command("run_jobs", "--job", "verify_audit_seals")


def test_scheduler_loop_stops_cleanly_on_sigterm(capsys):
    handlers = {}

    def fake_sleep(_seconds):  # SIGTERM arrives while the scheduler waits after its first pass
        handlers[signal.SIGTERM]()

    with mock.patch("apps.core.management.commands.run_jobs.signal.signal",
                    side_effect=lambda sig, handler: handlers.__setitem__(sig, handler)), \
            mock.patch("apps.core.management.commands.run_jobs.time.sleep", side_effect=fake_sleep), \
            mock.patch("apps.core.jobs.run_due", return_value=[]) as run_due:
        call_command("run_jobs", "--interval", "5")
    assert run_due.call_count == 1
    out = capsys.readouterr().out
    assert "Scheduler started" in out and "Scheduler stopped" in out


def test_operations_page_is_for_backup_managers_only(client):
    superadmin = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    enrol(superadmin)
    jobs.run_job(jobs.JOBS_BY_NAME["seal_audit_log"])
    login(client, superadmin)
    response = client.get(reverse("administration:operations"))
    assert response.status_code == 200
    assert b"restore_test" in response.content and b"overdue" in response.content
    assert reverse("administration:operations").encode() in client.get(reverse("core:dashboard")).content
    filtered = client.get(reverse("administration:operations"), {"job": "seal_audit_log"})
    assert b"Run history: seal_audit_log" in filtered.content

    admin = f.user(Role.ADMIN, groups=["IT Support"])
    enrol(admin)
    client.logout()
    login(client, admin)
    assert client.get(reverse("administration:operations")).status_code in (403, 404)
    assert reverse("administration:operations").encode() not in client.get(reverse("core:dashboard")).content

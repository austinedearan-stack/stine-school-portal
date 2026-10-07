"""Operator commands, queue scopes for approvers, async mail and staff announcement forms (Phase 13)."""

from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from apps.accounts import mfa
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.notifications.forms import AnnouncementForm
from apps.notifications.models import OutboundEmail
from apps.notifications.services import notify, send_outbox
from apps.student_requests import selectors as request_selectors
from apps.student_requests import services as request_services
from apps.student_requests.models import RequestCategory
from tests import factories as f
from tests.helpers import enrol

pytestmark = pytest.mark.django_db


# --- Commands -------------------------------------------------------------------------------------


def test_sync_capabilities_restores_group_permissions(capsys):
    from django.contrib.auth.models import Group

    group = Group.objects.get(name="Auditor")
    group.permissions.clear()
    call_command("sync_capabilities")
    assert group.permissions.filter(codename="view_audit_logs").exists()


def test_issue_mfa_enrollment_code_command(capsys):
    admin = f.user(Role.ADMIN)
    call_command("issue_mfa_enrollment_code", admin.username)
    code = capsys.readouterr().out.rsplit(": ", 1)[1].strip()
    assert mfa.check_enrollment_code(admin, code)
    enrol(admin)
    with pytest.raises(Exception):  # noqa: B017 - CommandError: device already exists
        call_command("issue_mfa_enrollment_code", admin.username)
    with pytest.raises(Exception):  # noqa: B017 - unknown user
        call_command("issue_mfa_enrollment_code", "nobody")


def test_seal_and_verify_commands(capsys):
    from apps.core.audit import record_audit_event

    record_audit_event(None, "TEST.SEAL")
    with mock.patch("apps.core.seals.timezone.now", return_value=timezone.now() + timedelta(hours=1)):
        call_command("seal_audit_log")
    assert "sealed" in capsys.readouterr().out
    call_command("verify_audit_seals")
    assert "verified" in capsys.readouterr().out
    call_command("seal_audit_log")
    assert "Nothing to seal" in capsys.readouterr().out


def test_verify_command_exits_non_zero_on_tampering():
    with mock.patch("apps.core.management.commands.verify_audit_seals.verify_all", return_value=["AUDIT: broken"]):
        with pytest.raises(SystemExit) as exit_info:
            call_command("verify_audit_seals")
    assert exit_info.value.code == 1


def test_outbox_failure_is_recorded_and_retried(settings):
    settings.NOTIFICATION_EMAIL_KINDS = ["SYSTEM"]
    notify(f.user(), "SYSTEM", "Hello")
    with mock.patch("django.core.mail.send_mail", side_effect=ConnectionError("smtp down")):
        assert send_outbox() == (0, 1)
    message = OutboundEmail.objects.get()
    assert message.attempts == 1 and message.last_error == "ConnectionError" and message.sent_at is None
    assert send_outbox() == (1, 0)


def test_security_mail_is_sent_off_the_request_thread(settings):
    from apps.core import mail as portal_mail

    settings.EMAIL_SEND_SYNC = False
    future_holder = {}

    def fake_submit(fn, *args):
        future_holder["call"] = (fn, args)

    with mock.patch.object(portal_mail._executor, "submit", side_effect=fake_submit):
        portal_mail.send_security_mail("Subject", "Body", "x@example.test")
    fn, args = future_holder["call"]
    fn(*args)  # run what the worker thread would run
    assert mail.outbox[-1].subject == "Subject"
    portal_mail.send_security_mail("Ignored", "Body", "")  # no recipient: nothing queued


# --- Request queue scopes -------------------------------------------------------------------------


def test_queue_scope_for_each_kind_of_staff():
    student = f.student()
    general = request_services.submit(student.user, student, RequestCategory.objects.get(code="GENERAL"), "a", "b",
                                      SYSTEM)
    transcript = request_services.submit(student.user, student, RequestCategory.objects.get(code="TRANSCRIPT"), "c",
                                         "d", SYSTEM)
    transfer = request_services.submit(student.user, student, RequestCategory.objects.get(code="TRANSFER"), "e", "f",
                                       SYSTEM, transfer={"transfer_type": "CAMPUS", "to_campus": "North"})

    def seen(user):
        return set(request_selectors.queue(user, status="").values_list("pk", flat=True))

    head = f.staff(department=student.program.department, groups=["Heads of Department"]).user
    assert seen(head) == {transcript.pk}  # approval categories in their department only
    board = f.user(Role.ADMIN, groups=["Academic Board"])
    assert seen(board) == {transfer.pk}
    registrar = f.user(Role.ADMIN, groups=["Registrar"])
    assert seen(registrar) == {transfer.pk}  # execute_transfers sees transfers
    services_admin = f.user(Role.ADMIN, groups=["Student Services"])
    assert seen(services_admin) == {general.pk, transcript.pk, transfer.pk}
    assert seen(f.staff().user) == set()


# --- Announcement form for staff ------------------------------------------------------------------


def test_staff_announcement_form_only_offers_allowed_targets():
    staff = f.staff(groups=["Communications"])
    other_department = f.department()
    form = AnnouncementForm(user=staff.user)
    assert "faculty" not in form.fields and "program" not in form.fields
    assert [v for v, _ in form.fields["scope"].choices] == ["DEPARTMENT", "OFFERING", "CLUB", "INDIVIDUAL"]
    assert list(form.fields["department"].queryset) == [staff.department]
    bound = AnnouncementForm({"title": "t", "body": "b", "audience_roles": "EVERYONE", "scope": "DEPARTMENT",
                              "department": other_department.pk, "publish_at": "2030-01-01T10:00"}, user=staff.user)
    assert not bound.is_valid()


def test_individual_recipients_are_resolved_by_number():
    admin = f.user(Role.ADMIN, groups=["Communications"])
    student = f.student()
    form = AnnouncementForm({"title": "t", "body": "b", "audience_roles": "EVERYONE", "scope": "INDIVIDUAL",
                             "recipient_numbers": f"{student.student_number}, nobody-here",
                             "publish_at": "2030-01-01T10:00"}, user=admin)
    assert not form.is_valid() and "Unknown recipient: nobody-here" in str(form.errors)
    form = AnnouncementForm({"title": "t", "body": "b", "audience_roles": "EVERYONE", "scope": "INDIVIDUAL",
                             "recipient_numbers": student.student_number, "publish_at": "2030-01-01T10:00"}, user=admin)
    assert form.is_valid() and form.recipients == [student.user]

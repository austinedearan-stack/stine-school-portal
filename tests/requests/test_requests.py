"""Requests and transfers: numbering, scoping, state machine, messages, attachments, transfers
(Phase 9; spec §11–13; audit Z-4, Z-5, Z-8, Z-9, F-4)."""

import io
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.core.models import AuditLog
from apps.student_requests import services
from apps.student_requests.models import RequestCategory, RequestStatus, StudentRequest
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db
S = RequestStatus
RequestError = services.RequestError


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def category(code="GENERAL"):
    return RequestCategory.objects.get(code=code)


def submit(student, code="GENERAL", **kw):
    return services.submit(student.user, student, category(code), kw.pop("subject", "Help"),
                           kw.pop("description", "Please help."), SYSTEM, **kw)


def reviewer(department, *groups):
    staff = f.staff(department=department, groups=groups or ("Department Reviewers",))
    return staff.user


def png_upload(name="evidence.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (1, 2, 3)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


# --- Numbering, routing, priority -----------------------------------------------------------------


def test_numbers_are_sequential_per_year():
    s = f.student()
    numbers = [submit(s).number for _ in range(3)]
    year = timezone.now().year
    assert numbers == [f"REQ-{year}-000001", f"REQ-{year}-000002", f"REQ-{year}-000003"]


def test_priority_and_department_come_from_the_category_and_student():
    s = f.student()
    req = submit(s, "UNIT_REGISTRATION_ISSUE")
    assert req.priority == "HIGH" and req.department == s.program.department


def test_student_cannot_set_priority_or_status_via_post(client):
    s = f.student()
    login(client, s.user)
    client.post(reverse("requests:create"), {"category": category().pk, "subject": "x", "description": "y",
                                             "priority": "URGENT", "status": "APPROVED", "assigned_to": "x"})
    req = StudentRequest.objects.get(student=s)
    assert req.priority == "LOW" and req.status == S.SUBMITTED and req.assigned_to is None


def test_reviewers_in_the_department_are_notified():
    s = f.student()
    rev = reviewer(s.program.department)
    submit(s)
    assert rev.notifications.filter(kind="REQUEST_STATUS").exists()


# --- Visibility -----------------------------------------------------------------------------------


def detail(client, req):
    return client.get(reverse("requests:detail", args=[req.pk]))


def test_visibility_rules(client):
    s = f.student()
    req = submit(s)
    login(client, s.user)
    assert detail(client, req).status_code == 200
    login(client, f.student().user)
    assert detail(client, req).status_code == 404  # another student
    login(client, reviewer(s.program.department))
    assert detail(client, req).status_code == 200  # reviewer of the routing department
    login(client, reviewer(f.department()))
    assert detail(client, req).status_code == 404  # reviewer of another department
    login(client, f.staff(department=s.program.department).user)
    assert detail(client, req).status_code == 404  # staff without review_requests
    services_admin = f.user(Role.ADMIN, groups=["Student Services"])
    enrol(services_admin)
    login(client, services_admin)
    assert detail(client, req).status_code == 200  # review_all_requests


def test_queue_only_lists_requests_in_scope(client):
    a, b = f.student(), f.student()
    mine, other = submit(a, subject="In my dept"), submit(b, subject="Elsewhere")
    login(client, reviewer(a.program.department))
    page = client.get(reverse("requests:queue")).content.decode()
    assert mine.number in page and other.number not in page


# --- State machine (audit Z-4) --------------------------------------------------------------------


def test_reviewer_flow_with_information_request_and_resolution():
    s = f.student()
    req = submit(s)
    rev = reviewer(s.program.department)
    services.transition(rev, req, S.UNDER_REVIEW, "", SYSTEM)
    with pytest.raises(RequestError, match="note"):
        services.transition(rev, req, S.NEEDS_INFORMATION, "", SYSTEM)
    services.transition(rev, req, S.NEEDS_INFORMATION, "Which semester?", SYSTEM)
    services.add_message(s.user, req, "Semester 1", SYSTEM)
    req.refresh_from_db()
    assert req.status == S.UNDER_REVIEW  # the student's reply moves it back automatically
    services.transition(rev, req, S.RESOLVED, "Fixed in the system.", SYSTEM)
    req.refresh_from_db()
    assert req.status == S.RESOLVED and req.resolution == "Fixed in the system."
    history = list(req.status_changes.values_list("to_status", flat=True))
    assert history == [S.SUBMITTED, S.UNDER_REVIEW, S.NEEDS_INFORMATION, S.UNDER_REVIEW, S.RESOLVED]
    assert s.user.notifications.filter(kind="REQUEST_STATUS").count() >= 4


@pytest.mark.parametrize("target", [S.APPROVED, S.RESOLVED, S.CLOSED, S.UNDER_REVIEW, "DELETED"])
def test_student_can_only_cancel(target):
    s = f.student()
    req = submit(s)
    with pytest.raises(RequestError):
        services.transition(s.user, req, target, "x", SYSTEM)
    services.transition(s.user, req, S.CANCELLED, "", SYSTEM)


def test_invalid_jump_is_refused():
    s = f.student()
    req = submit(s)
    rev = reviewer(s.program.department)
    with pytest.raises(RequestError):
        services.transition(rev, req, S.RESOLVED, "skip review", SYSTEM)  # SUBMITTED -> RESOLVED is not allowed
    req.refresh_from_db()
    assert req.status == S.SUBMITTED


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True, databases=["default", "audit"])
@override_settings(AUDIT_INDEPENDENT_CONNECTION=True)
def test_refused_transition_is_recorded_even_though_the_transaction_rolls_back():
    s = f.student()
    req = submit(s)
    with pytest.raises(RequestError):
        services.transition(reviewer(s.program.department), req, S.RESOLVED, "skip review", SYSTEM)
    entry = AuditLog.objects.get(action="REQUEST.TRANSITION", outcome="DENIED")
    assert entry.object_id == str(req.pk)


def test_approval_categories_need_an_approver_not_a_reviewer():
    s = f.student()
    req = submit(s, "TRANSCRIPT")  # requires approval (approve_requests)
    rev = reviewer(s.program.department)
    services.transition(rev, req, S.UNDER_REVIEW, "", SYSTEM)
    with pytest.raises(RequestError):
        services.transition(rev, req, S.RESOLVED, "done", SYSTEM)  # approval categories are never "resolved"
    with pytest.raises(RequestError):
        services.transition(rev, req, S.APPROVED, "", SYSTEM)
    head = reviewer(s.program.department, "Heads of Department")
    services.transition(head, req, S.APPROVED, "", SYSTEM)
    req.refresh_from_db()
    assert req.status == S.APPROVED and req.decided_by == head


def test_head_of_another_department_cannot_approve():
    s = f.student()
    req = submit(s, "TRANSCRIPT")
    services.transition(reviewer(s.program.department), req, S.UNDER_REVIEW, "", SYSTEM)
    with pytest.raises(PermissionDenied):
        services.transition(reviewer(f.department(), "Heads of Department"), req, S.APPROVED, "", SYSTEM)


def test_view_rejects_arbitrary_status_from_post(client):
    s = f.student()
    req = submit(s)
    login(client, reviewer(s.program.department))
    client.post(reverse("requests:transition", args=[req.pk]), {"target": "APPROVED", "note": "x"})
    req.refresh_from_db()
    assert req.status == S.SUBMITTED


# --- Messages and attachments (audit Z-9) ---------------------------------------------------------


def test_no_messages_on_closed_requests():
    s = f.student()
    req = submit(s)
    services.transition(s.user, req, S.CANCELLED, "", SYSTEM)
    with pytest.raises(PermissionDenied):
        services.add_message(s.user, req, "hello?", SYSTEM)


def test_internal_notes_are_hidden_from_the_student(client):
    s = f.student()
    req = submit(s)
    rev = reviewer(s.program.department)
    services.add_message(rev, req, "Looks like a duplicate of last week", SYSTEM, internal=True)
    with pytest.raises(RequestError):
        services.add_message(s.user, req, "sneaky", SYSTEM, internal=True)
    login(client, s.user)
    assert b"duplicate of last week" not in detail(client, req).content
    login(client, rev)
    assert b"duplicate of last week" in detail(client, req).content


def test_attachment_limits_and_access(client):
    s = f.student()
    req = submit(s, uploads=[png_upload()])
    with pytest.raises(RequestError, match="At most"):
        services.add_message(s.user, req, "more", SYSTEM, uploads=[png_upload(f"{i}.png") for i in range(3)])
    attachment = req.attachments.get()
    url = reverse("requests:attachment", args=[attachment.pk])
    login(client, s.user)
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Disposition"].startswith("attachment")
    login(client, f.student().user)
    assert client.get(url).status_code == 404
    login(client, reviewer(s.program.department))
    assert client.get(url).status_code == 200


def test_malicious_attachment_is_rejected():
    s = f.student()
    bad = SimpleUploadedFile("cv.pdf", b"%PDF-1.4\n1 0 obj << /OpenAction << /JS (x) >> >>\n%%EOF", "application/pdf")
    with pytest.raises(RequestError, match="cv.pdf"):
        submit(s, uploads=[bad])
    assert not StudentRequest.objects.filter(student=s).exists()  # the whole submission rolled back


# --- Assignment -----------------------------------------------------------------------------------


def test_assignment_only_to_in_scope_reviewers():
    s = f.student()
    req = submit(s)
    rev = reviewer(s.program.department)
    colleague = f.staff(department=s.program.department, groups=["Department Reviewers"])
    services.assign(rev, req, colleague, SYSTEM)
    assert colleague.user.notifications.filter(title__contains="assigned").exists()
    with pytest.raises(RequestError):
        services.assign(rev, req, f.staff(department=f.department(), groups=["Department Reviewers"]), SYSTEM)
    with pytest.raises(RequestError):
        services.assign(rev, req, f.staff(department=s.program.department), SYSTEM)  # not a reviewer


# --- Transfers (audit Z-5) ------------------------------------------------------------------------


def transfer_request(student, **target):
    target.setdefault("to_program", f.program())
    return submit(student, "TRANSFER", transfer={"transfer_type": "PROGRAM", "reason": "Interest", **target})


def approve(req):
    services.transition(reviewer(req.department), req, S.UNDER_REVIEW, "", SYSTEM)
    board = f.user(Role.ADMIN, groups=["Academic Board"])
    services.transition(board, req, S.APPROVED, "", SYSTEM)
    return board


def test_approving_a_transfer_does_not_change_the_record():
    s = f.student()
    original = s.program
    req = transfer_request(s)
    approve(req)
    s.refresh_from_db()
    assert s.program == original


def test_execute_once_with_separation_of_duties():
    s = f.student()
    target = f.program()
    req = transfer_request(s, to_program=target)
    approve(req)
    registrar = f.user(Role.ADMIN, groups=["Registrar"])
    services.execute_transfer(registrar, req, SYSTEM)
    s.refresh_from_db()
    assert s.program == target
    with pytest.raises(RequestError, match="already"):
        services.execute_transfer(registrar, req, SYSTEM)
    assert AuditLog.objects.filter(action="TRANSFER.EXECUTED", object_id=str(s.pk)).exists()


def test_approver_cannot_execute_own_approval():
    s = f.student()
    req = transfer_request(s)
    board = approve(req)
    board.groups.add(Group.objects.get(name="Registrar"))  # the approver also holds execute_transfers
    with pytest.raises(RequestError, match="approved"):
        services.execute_transfer(User.objects.get(pk=board.pk), req, SYSTEM)


def test_execution_refused_if_record_changed_since_request():
    s = f.student()
    req = transfer_request(s)
    approve(req)
    s.campus = "North Campus"
    s.save()
    with pytest.raises(RequestError, match="changed"):
        services.execute_transfer(f.user(Role.ADMIN, groups=["Registrar"]), req, SYSTEM)


def test_unapproved_transfer_cannot_be_executed():
    s = f.student()
    req = transfer_request(s)
    with pytest.raises(RequestError):
        services.execute_transfer(f.user(Role.ADMIN, groups=["Registrar"]), req, SYSTEM)


def test_transfer_needs_a_real_target():
    s = f.student()
    with pytest.raises(RequestError):
        submit(s, "TRANSFER", transfer={"transfer_type": "PROGRAM", "to_program": s.program})
    with pytest.raises(RequestError):
        submit(s, "TRANSFER", transfer={"transfer_type": "CAMPUS", "to_campus": s.campus})


def test_unexecuted_approved_transfer_is_never_auto_closed_or_closed_by_reviewers():
    s = f.student()
    req = transfer_request(s)
    approve(req)
    StudentRequest.objects.filter(pk=req.pk).update(updated_at=timezone.now() - timedelta(days=30))
    call_command("close_stale_requests")
    req.refresh_from_db()
    assert req.status == S.APPROVED
    with pytest.raises(RequestError):
        services.transition(reviewer(req.department), req, S.CLOSED, "", SYSTEM)


def test_finished_requests_close_after_idle_period():
    s = f.student()
    req = submit(s)
    rev = reviewer(s.program.department)
    services.transition(rev, req, S.UNDER_REVIEW, "", SYSTEM)
    services.transition(rev, req, S.RESOLVED, "done", SYSTEM)
    StudentRequest.objects.filter(pk=req.pk).update(updated_at=timezone.now() - timedelta(days=15))
    call_command("close_stale_requests")
    req.refresh_from_db()
    assert req.status == S.CLOSED


def test_request_settings_need_the_capability(client):
    login(client, f.student().user)
    assert client.get(reverse("requests:categories")).status_code == 403

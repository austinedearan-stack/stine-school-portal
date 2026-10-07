"""Admin panel: capability gating, account creation, records, academic setup, audit viewers (Phase 10)."""

import pytest
from django.contrib.auth.models import Group
from django.core import mail
from django.urls import reverse

from apps.academics.models import Faculty, Semester, UnitPrerequisite
from apps.accounts.models import StudentProfile, User
from apps.administration import services
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.core.models import AuditLog
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


def admin(*groups, role=Role.ADMIN):
    user = f.user(role, groups=groups)
    enrol(user)
    return user


# --- Gating ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("url_name, group", [
    ("administration:dashboard", "Auditor"),
    ("administration:users", "IT Support"),
    ("administration:audit_log", "Auditor"),
    ("administration:security_events", "Auditor"),
    ("administration:academics", "Academic Office"),
])
def test_pages_need_their_capability(client, url_name, group):
    login(client, admin())  # an admin with no capabilities (audit Z-6)
    assert client.get(reverse(url_name)).status_code == 403
    login(client, f.student().user)
    assert client.get(reverse(url_name)).status_code == 403
    login(client, admin(group))
    assert client.get(reverse(url_name)).status_code == 200


def test_staff_with_unrelated_capability_cannot_open_setup_kinds(client):
    login(client, admin("Academic Office"))
    assert client.get(reverse("administration:setup_list", args=["units"])).status_code == 200
    login(client, admin("Examinations"))
    assert client.get(reverse("administration:setup_list", args=["units"])).status_code == 403


# --- Accounts -------------------------------------------------------------------------------------


def test_registrar_creates_student_who_sets_own_password(client):
    program = f.program()
    login(client, admin("Registrar"))
    response = client.post(reverse("administration:student_create"), {
        "username": "S260999", "email": "s260999@example.test", "first_name": "New", "last_name": "Student",
        "student_number": "S260999", "program": program.pk, "year_of_study": 1, "current_semester_number": 1,
        "admission_date": "2026-09-01", "academic_status": "ACTIVE", "disciplinary_status": "CLEAR",
        "campus": "Main Campus", "gender": ""})
    assert response.status_code == 302
    user = User.objects.get(username="S260999")
    assert not user.has_usable_password() and user.must_change_password and user.role == Role.STUDENT
    assert any("reset code" in m.subject for m in mail.outbox)
    assert AuditLog.objects.filter(action="ACCOUNT.STUDENT_CREATED").exists()


def test_staff_admin_cannot_create_admin_accounts(client):
    hr = admin("HR")
    with pytest.raises(Exception):  # noqa: B017 - PermissionDenied from authorize
        services.create_staff(hr, {"username": "a1", "email": "a1@example.test", "first_name": "A", "last_name": "B",
                                   "staff_number": "E9", "department": f.department()}, Role.ADMIN, SYSTEM)
    login(client, hr)
    form = client.get(reverse("administration:staff_create")).context["form"]
    assert [value for value, _ in form.fields["role"].choices] == [Role.STAFF]


def test_superadmin_can_create_admin_accounts():
    sa = admin("Superadmin", role=Role.SUPERADMIN)
    staff = services.create_staff(sa, {"username": "adm9", "email": "adm9@example.test", "first_name": "A",
                                       "last_name": "B", "staff_number": "E99", "department": f.department()},
                                  Role.ADMIN, SYSTEM)
    assert staff.user.role == Role.ADMIN


def test_student_record_edit_is_audited_and_notifies(client):
    student = f.student()
    other_program = f.program()
    login(client, admin("Registrar"))
    data = {"first_name": student.user.first_name, "last_name": student.user.last_name, "email": student.user.email,
            "student_number": student.student_number, "program": other_program.pk, "year_of_study": 2,
            "current_semester_number": 1, "admission_date": "2024-09-01", "academic_status": "PROBATION",
            "disciplinary_status": "CLEAR", "campus": "Main Campus", "gender": ""}
    assert client.post(reverse("administration:student_edit", args=[student.pk]), data).status_code == 302
    student.refresh_from_db()
    assert student.program == other_program and student.academic_status == "PROBATION"
    entry = AuditLog.objects.get(action="STUDENT.RECORD_UPDATED")
    assert entry.changes["before"]["academic_status"] == "ACTIVE" and entry.changes["after"]["year_of_study"] == 2
    assert student.user.notifications.filter(title__contains="record").exists()


def test_department_change_notifies_staff_member():
    staff = f.staff()
    new_department = f.department()
    services.update_staff_record(admin("HR"), staff, {"department": new_department}, SYSTEM)
    assert staff.user.notifications.filter(title="Your department changed").exists()


def test_role_and_group_changes_through_the_ui(client):
    sa = admin("Superadmin", role=Role.SUPERADMIN)
    target = f.user(Role.STAFF)
    login(client, sa)
    client.post(reverse("administration:user_action", args=[target.pk, "groups"]),
                {"groups": [Group.objects.get(name="Timetabling").pk]})
    assert target.groups.filter(name="Timetabling").exists()
    # Registrar is outside the STAFF ceiling: refused.
    client.post(reverse("administration:user_action", args=[target.pk, "groups"]),
                {"groups": [Group.objects.get(name="Registrar").pk]})
    assert not target.groups.filter(name="Registrar").exists()
    client.post(reverse("administration:user_action", args=[target.pk, "role"]), {"role": Role.ADMIN})
    target.refresh_from_db()
    assert target.role == Role.ADMIN and target.groups.count() == 0


def test_mfa_reset_shows_code_once_and_never_emails_it(client):
    target = admin("Auditor")
    login(client, admin("Superadmin", role=Role.SUPERADMIN))
    response = client.post(reverse("administration:user_action", args=[target.pk, "reset-mfa"]))
    code = response.context["code"]
    assert all(code not in m.body for m in mail.outbox)


def test_it_support_cannot_touch_admin_accounts(client):
    victim = admin("Auditor")
    login(client, admin("IT Support"))
    client.post(reverse("administration:user_action", args=[victim.pk, "deactivate"]))
    victim.refresh_from_db()
    assert victim.is_active


# --- Academic setup -------------------------------------------------------------------------------


def test_setup_create_and_edit_is_audited(client):
    login(client, admin("Academic Office"))
    client.post(reverse("administration:setup_create", args=["faculties"]),
                {"code": "FLAW", "name": "Faculty of Law", "is_active": "on"})
    faculty = Faculty.objects.get(code="FLAW")
    client.post(reverse("administration:setup_edit", args=["faculties", faculty.pk]),
                {"code": "FLAW", "name": "School of Law", "is_active": "on"})
    faculty.refresh_from_db()
    assert faculty.name == "School of Law"
    entry = AuditLog.objects.get(action="SETUP.FACULTY_UPDATED")
    assert entry.changes["before"]["name"] == "Faculty of Law"


def test_prerequisite_cycles_are_refused():
    office = admin("Academic Office")
    a, b, c = f.unit(), f.unit(), f.unit()
    services.add_prerequisite(office, b, a, SYSTEM)  # a before b
    services.add_prerequisite(office, c, b, SYSTEM)  # b before c
    with pytest.raises(services.AdminError, match="cycle"):
        services.add_prerequisite(office, a, c, SYSTEM)  # c before a would close the loop
    assert UnitPrerequisite.objects.count() == 2


def test_only_one_current_semester():
    office = admin("Academic Office")
    first, second = f.semester(is_current=True), f.semester()
    services.set_current_semester(office, second, SYSTEM)
    assert list(Semester.objects.filter(is_current=True)) == [second]
    first.refresh_from_db()
    assert not first.is_current


def test_audit_log_filter(client):
    student = f.student()
    services.update_student_record(admin("Registrar"), student, {"year_of_study": 3}, SYSTEM)
    login(client, admin("Auditor"))
    page = client.get(reverse("administration:audit_log"), {"action": "STUDENT.RECORD"}).content.decode()
    assert "STUDENT.RECORD_UPDATED" in page


def test_student_records_cannot_be_edited_by_students(client):
    student = f.student()
    login(client, student.user)
    assert client.post(reverse("administration:student_edit", args=[student.pk]), {}).status_code == 403
    assert StudentProfile.objects.get(pk=student.pk).academic_status == "ACTIVE"

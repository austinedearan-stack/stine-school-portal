"""Unit registration rules, drop, override and grading (Phase 5; spec §7)."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.academics import services
from apps.academics.models import OfferingStatus, RegistrationStatus, UnitPrerequisite, UnitRegistration
from apps.accounts.models import AcademicStatus
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.core.models import AuditLog
from apps.timetable.models import TimetableEntry, Venue
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db
RegistrationError = services.RegistrationError


@pytest.fixture
def semester():
    return f.semester(is_current=True)


def reg(student, offering):
    return services.register(student.user, student, offering, SYSTEM)


def test_student_registers_and_is_notified(semester):
    student = f.student()
    offering = f.offering(semester=semester)
    registration = reg(student, offering)
    assert registration.status == RegistrationStatus.REGISTERED and registration.registered_by is None
    assert student.user.notifications.filter(kind="REGISTRATION").exists()
    assert AuditLog.objects.filter(action="REGISTRATION.REGISTER", object_id=str(registration.pk)).exists()


def test_window_must_be_open():
    semester = f.semester(open_registration=False, is_current=True)
    with pytest.raises(RegistrationError, match="not open"):
        reg(f.student(), f.offering(semester=semester))


@pytest.mark.parametrize("status", [OfferingStatus.DRAFT, OfferingStatus.CLOSED, OfferingStatus.CANCELLED])
def test_offering_must_be_open(semester, status):
    with pytest.raises(RegistrationError):
        reg(f.student(), f.offering(semester=semester, status=status))


@pytest.mark.parametrize("status", [AcademicStatus.SUSPENDED, AcademicStatus.WITHDRAWN, AcademicStatus.GRADUATED])
def test_blocked_academic_status(semester, status):
    with pytest.raises(RegistrationError, match="academic status"):
        reg(f.student(academic_status=status), f.offering(semester=semester))


def test_capacity_is_enforced(semester):
    offering = f.offering(semester=semester, capacity=1)
    reg(f.student(), offering)
    with pytest.raises(RegistrationError, match="full"):
        reg(f.student(), offering)


def test_credit_limit_is_enforced(semester):
    student = f.student(program=f.program(max_credits_per_semester=6))
    reg(student, f.offering(semester=semester, unit=f.unit(credit_hours=4)))
    with pytest.raises(RegistrationError, match="limit is 6"):
        reg(student, f.offering(semester=semester, unit=f.unit(credit_hours=3)))


def test_prerequisites_are_enforced(semester):
    student = f.student()
    basic, advanced = f.unit(code="BAS101"), f.unit(code="ADV201")
    UnitPrerequisite.objects.create(unit=advanced, prerequisite=basic)
    offering = f.offering(semester=semester, unit=advanced)
    with pytest.raises(RegistrationError, match="BAS101"):
        reg(student, offering)
    earlier = f.offering(unit=basic)  # completed with a pass in an earlier semester
    UnitRegistration.objects.create(student=student, offering=earlier, unit=basic, semester=earlier.semester,
                                    status=RegistrationStatus.COMPLETED, grade="C")
    reg(student, offering)


def test_failed_prerequisite_does_not_count(semester):
    student = f.student()
    basic, advanced = f.unit(), f.unit()
    UnitPrerequisite.objects.create(unit=advanced, prerequisite=basic)
    earlier = f.offering(unit=basic)
    UnitRegistration.objects.create(student=student, offering=earlier, unit=basic, semester=earlier.semester,
                                    status=RegistrationStatus.FAILED, grade="E")
    with pytest.raises(RegistrationError):
        reg(student, f.offering(semester=semester, unit=advanced))


def test_program_restriction_and_minimum_year(semester):
    student = f.student(year_of_study=1)
    restricted = f.offering(semester=semester)
    restricted.eligible_programs.add(f.program())
    with pytest.raises(RegistrationError, match="restricted"):
        reg(student, restricted)
    with pytest.raises(RegistrationError, match="year 3"):
        reg(student, f.offering(semester=semester, min_year=3))


def test_two_sections_of_the_same_unit_are_refused(semester):
    student = f.student()
    a = f.offering(semester=semester)
    b = f.offering(semester=semester, unit=a.unit, section="B")
    reg(student, a)
    with pytest.raises(RegistrationError, match="already registered"):
        reg(student, b)


def test_timetable_clash_is_refused(semester):
    student = f.student()
    venue1, venue2 = Venue.objects.create(code="V1", name="V1"), Venue.objects.create(code="V2", name="V2")
    a, b = f.offering(semester=semester), f.offering(semester=semester)
    TimetableEntry.objects.create(offering=a, semester=semester, venue=venue1, day_of_week=1,
                                  start_time=f.at(9), end_time=f.at(11))
    TimetableEntry.objects.create(offering=b, semester=semester, venue=venue2, day_of_week=1,
                                  start_time=f.at(10), end_time=f.at(12))
    reg(student, a)
    with pytest.raises(RegistrationError, match="clashes"):
        reg(student, b)


def test_student_cannot_register_someone_else(semester):
    me, other = f.student(), f.student()
    with pytest.raises(PermissionDenied):
        services.register(me.user, other, f.offering(semester=semester), SYSTEM)


# --- Drop -----------------------------------------------------------------------------------------


def test_drop_and_reregister(semester):
    student = f.student()
    offering = f.offering(semester=semester)
    registration = reg(student, offering)
    services.drop(student.user, registration, SYSTEM)
    registration.refresh_from_db()
    assert registration.status == RegistrationStatus.DROPPED and registration.dropped_at
    reg(student, offering)  # allowed again after dropping


def test_drop_after_deadline_is_refused(semester):
    student = f.student()
    registration = reg(student, f.offering(semester=semester))
    type(semester).objects.filter(pk=semester.pk).update(add_drop_deadline=timezone.now() - timedelta(minutes=1))
    with pytest.raises(RegistrationError, match="deadline"):
        services.drop(student.user, UnitRegistration.objects.get(pk=registration.pk), SYSTEM)


def test_minimum_credit_rule_on_drop(semester):
    student = f.student(program=f.program(min_credits_per_semester=5))
    keep = reg(student, f.offering(semester=semester, unit=f.unit(credit_hours=3)))
    reg(student, f.offering(semester=semester, unit=f.unit(credit_hours=3)))
    with pytest.raises(RegistrationError, match="minimum is 5"):
        services.drop(student.user, keep, SYSTEM)


# --- Override -------------------------------------------------------------------------------------


def test_registrar_override_outside_window_needs_reason_and_is_audited():
    semester = f.semester(open_registration=False, is_current=True)
    student = f.student()
    offering = f.offering(semester=semester)
    registrar = f.user(Role.ADMIN, groups=["Registrar"])
    with pytest.raises(RegistrationError, match="reason"):
        services.register(registrar, student, offering, SYSTEM)
    registration = services.register(registrar, student, offering, SYSTEM, override_reason="Late medical clearance")
    assert registration.registered_by == registrar
    entry = AuditLog.objects.get(action="REGISTRATION.OVERRIDE_REGISTER")
    assert entry.changes["after"]["reason"] == "Late medical clearance"


def test_override_still_applies_capacity():
    semester = f.semester(open_registration=False, is_current=True)
    offering = f.offering(semester=semester, capacity=1)
    registrar = f.user(Role.ADMIN, groups=["Registrar"])
    services.register(registrar, f.student(), offering, SYSTEM, override_reason="x")
    with pytest.raises(RegistrationError, match="full"):
        services.register(registrar, f.student(), offering, SYSTEM, override_reason="x")


def test_admin_without_manage_students_cannot_override(semester):
    admin = f.user(Role.ADMIN, groups=["Timetabling"])
    with pytest.raises(PermissionDenied):
        services.register(admin, f.student(), f.offering(semester=semester), SYSTEM, override_reason="x")


# --- Grades ---------------------------------------------------------------------------------------


def test_lecturer_grades_own_offering_once(semester):
    lecturer = f.staff(groups=["Lecturers"])
    offering = f.offering(semester=semester, lecturer=lecturer)
    registration = reg(f.student(), offering)
    services.record_grade(lecturer.user, registration, "B", SYSTEM)
    registration.refresh_from_db()
    assert registration.status == RegistrationStatus.COMPLETED and str(registration.grade_points) == "3.00"
    with pytest.raises(RegistrationError, match="amendments"):
        services.record_grade(lecturer.user, registration, "A", SYSTEM)


def test_lecturer_cannot_grade_other_offerings(semester):
    lecturer = f.staff(groups=["Lecturers"])
    registration = reg(f.student(), f.offering(semester=semester, lecturer=f.staff()))
    with pytest.raises(PermissionDenied):
        services.record_grade(lecturer.user, registration, "A", SYSTEM)


def test_examinations_office_amends_with_audit(semester):
    lecturer = f.staff(groups=["Lecturers"])
    registration = reg(f.student(), f.offering(semester=semester, lecturer=lecturer))
    services.record_grade(lecturer.user, registration, "C", SYSTEM)
    exams = f.user(Role.ADMIN, groups=["Examinations"])
    services.record_grade(exams, registration, "B", SYSTEM)
    entry = AuditLog.objects.get(action="GRADE.AMENDED")
    assert entry.changes["before"]["grade"] == "C" and entry.changes["after"]["grade"] == "B"


# --- Views (IDOR, CSRF-protected POST) -------------------------------------------------------------


def test_catalogue_search_and_registration_flow(client, semester):
    student = f.student()
    offering = f.offering(semester=semester, unit=f.unit(code="ZZZ999", title="Zymurgy"))
    f.offering(semester=semester, unit=f.unit(title="Other"))
    login(client, student.user)
    page = client.get(reverse("academics:catalog"), {"q": "zymur"}).content.decode()
    assert "Zymurgy" in page and "Other" not in page
    response = client.post(reverse("academics:register", args=[offering.pk]))
    assert response.status_code == 302
    assert UnitRegistration.objects.filter(student=student, offering=offering).exists()


def test_draft_offerings_are_invisible(client, semester):
    draft = f.offering(semester=semester, status=OfferingStatus.DRAFT)
    login(client, f.student().user)
    assert client.get(reverse("academics:offering_detail", args=[draft.pk])).status_code == 404


def test_student_cannot_drop_another_students_registration(client, semester):
    victim = f.student()
    registration = reg(victim, f.offering(semester=semester))
    attacker = f.student()
    login(client, attacker.user)
    assert client.post(reverse("academics:drop", args=[registration.pk])).status_code == 404
    registration.refresh_from_db()
    assert registration.status == RegistrationStatus.REGISTERED


def test_registration_requires_post(client, semester):
    offering = f.offering(semester=semester)
    login(client, f.student().user)
    assert client.get(reverse("academics:register", args=[offering.pk])).status_code == 405


def test_staff_cannot_use_student_registration(client, semester):
    login(client, f.staff().user)
    assert client.post(reverse("academics:register", args=[f.offering(semester=semester).pk])).status_code == 403


def test_class_list_is_limited_to_the_lecturer(client, semester):
    lecturer, other_lecturer = f.staff(), f.staff()
    offering = f.offering(semester=semester, lecturer=lecturer)
    reg(f.student(), offering)
    login(client, other_lecturer.user)
    assert client.get(reverse("academics:class_list", args=[offering.pk])).status_code == 404
    login(client, lecturer.user)
    assert client.get(reverse("academics:class_list", args=[offering.pk])).status_code == 200
    login(client, f.student().user)
    assert client.get(reverse("academics:class_list", args=[offering.pk])).status_code == 404


def test_student_cannot_post_grades(client, semester):
    student = f.student()
    registration = reg(student, f.offering(semester=semester))
    login(client, student.user)
    response = client.post(reverse("academics:record_grade", args=[registration.pk]), {"grade": "A"})
    assert response.status_code == 404
    registration.refresh_from_db()
    assert registration.grade == ""


def test_override_view_requires_capability(client, semester):
    admin = f.user(Role.ADMIN)
    enrol(admin)
    login(client, admin)
    offering = f.offering(semester=semester)
    response = client.post(reverse("academics:override", args=[offering.pk]),
                           {"student_number": f.student().student_number, "reason": "x"})
    assert response.status_code == 403

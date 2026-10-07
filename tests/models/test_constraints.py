"""Phase 2 exit criterion: database constraints (unique, partial unique, check) hold at the DB level.

Each violation is attempted with the ORM's ``create``/``update`` (bypassing form validation) and must
raise IntegrityError from the database itself.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academics.models import Program, RegistrationStatus, Semester, UnitPrerequisite, UnitRegistration
from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership, MembershipStatus
from apps.core.capabilities import Role
from apps.hostels.models import AllocationStatus, Bed, Hostel, HostelAllocation, HostelBuilding, HostelFloor, Room
from apps.notifications.models import Announcement, Scope
from apps.student_requests.models import RequestCategory, StudentRequest, TransferRequest, TransferType
from apps.timetable.models import TimetableEntry, Venue
from tests import factories as f

pytestmark = pytest.mark.django_db


def violates(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


# --- Accounts ------------------------------------------------------------------------------------


def test_is_superuser_can_never_be_true_even_via_queryset_update():
    u = f.user()
    violates(lambda: User.objects.filter(pk=u.pk).update(is_superuser=True))


def test_role_must_be_in_vocabulary():
    u = f.user()
    violates(lambda: User.objects.filter(pk=u.pk).update(role="GOD"))


def test_username_and_email_unique_case_insensitively():
    f.user(username="S1000", email="Alice@Example.test")
    violates(lambda: User.objects.create(username="s1000", email="other@example.test"))
    violates(lambda: User.objects.create(username="s2000", email="alice@example.TEST"))


def test_createsuperuser_path_is_disabled():
    with pytest.raises(NotImplementedError):
        User.objects.create_superuser("root", "root@example.test", "x")
    with pytest.raises(ValueError):
        User.objects.create_user("root2", "root2@example.test", "x", is_superuser=True)


def test_student_year_of_study_range():
    s = f.student()
    violates(lambda: type(s).objects.filter(pk=s.pk).update(year_of_study=9))
    violates(lambda: type(s).objects.filter(pk=s.pk).update(academic_status="EXPELLED_FOREVER"))


# --- Academics -----------------------------------------------------------------------------------


def test_program_min_credits_cannot_exceed_max():
    p = f.program()
    violates(lambda: Program.objects.filter(pk=p.pk).update(min_credits_per_semester=30, max_credits_per_semester=20))


def test_only_one_current_semester():
    f.semester(is_current=True)
    violates(lambda: f.semester(is_current=True))


def test_semester_registration_window_must_be_ordered():
    s = f.semester()
    violates(lambda: Semester.objects.filter(pk=s.pk).update(registration_closes_at=s.registration_opens_at))


def test_unit_cannot_be_its_own_prerequisite():
    u = f.unit()
    violates(lambda: UnitPrerequisite.objects.create(unit=u, prerequisite=u))


def test_one_live_registration_per_unit_and_semester():
    student = f.student()
    offering_a = f.offering()
    offering_b = f.offering(unit=offering_a.unit, semester=offering_a.semester, section="B")
    UnitRegistration.objects.create(
        student=student, offering=offering_a, unit=offering_a.unit, semester=offering_a.semester
    )
    # Section B of the same unit in the same semester is refused by the partial unique index.
    violates(
        lambda: UnitRegistration.objects.create(
            student=student, offering=offering_b, unit=offering_b.unit, semester=offering_b.semester
        )
    )


def test_dropped_registration_does_not_block_re_registration():
    student = f.student()
    offering = f.offering()
    first = UnitRegistration.objects.create(student=student, offering=offering, unit=offering.unit,
                                            semester=offering.semester)
    UnitRegistration.objects.filter(pk=first.pk).update(status=RegistrationStatus.DROPPED)
    UnitRegistration.objects.create(student=student, offering=offering, unit=offering.unit, semester=offering.semester)


def test_grade_must_be_in_allowed_set():
    student = f.student()
    offering = f.offering()
    reg = UnitRegistration.objects.create(student=student, offering=offering, unit=offering.unit,
                                          semester=offering.semester)
    violates(lambda: UnitRegistration.objects.filter(pk=reg.pk).update(grade="A+"))


# --- Timetable -----------------------------------------------------------------------------------


def test_timetable_entry_must_end_after_it_starts():
    offering = f.offering()
    venue = Venue.objects.create(code="LH1", name="Lecture hall 1")
    violates(
        lambda: TimetableEntry.objects.create(
            offering=offering, semester=offering.semester, venue=venue, day_of_week=1,
            start_time=f.at(10), end_time=f.at(9),
        )
    )


# --- Hostels -------------------------------------------------------------------------------------


def _bed():
    hostel = Hostel.objects.create(code=f"H{f.n()}", name=f"Hostel {f.n()}")
    building = HostelBuilding.objects.create(hostel=hostel, name="A")
    floor = HostelFloor.objects.create(building=building, level=1)
    room = Room.objects.create(floor=floor, number="101", capacity=2)
    return Bed.objects.create(room=room, label="A")


def test_bed_has_at_most_one_holder_per_semester():
    bed = _bed()
    semester = f.semester()
    HostelAllocation.objects.create(student=f.student(), bed=bed, semester=semester)
    violates(lambda: HostelAllocation.objects.create(student=f.student(), bed=bed, semester=semester))
    violates(
        lambda: HostelAllocation.objects.create(
            student=f.student(), bed=bed, semester=semester, status=AllocationStatus.PENDING_ACCEPTANCE
        )
    )


def test_student_holds_at_most_one_bed_per_semester():
    student = f.student()
    semester = f.semester()
    HostelAllocation.objects.create(student=student, bed=_bed(), semester=semester)
    violates(lambda: HostelAllocation.objects.create(student=student, bed=_bed(), semester=semester))


def test_ended_allocation_frees_the_bed():
    bed = _bed()
    semester = f.semester()
    first = HostelAllocation.objects.create(student=f.student(), bed=bed, semester=semester)
    HostelAllocation.objects.filter(pk=first.pk).update(status=AllocationStatus.VACATED)
    HostelAllocation.objects.create(student=f.student(), bed=bed, semester=semester)


def test_room_capacity_range():
    room = _bed().room
    violates(lambda: Room.objects.filter(pk=room.pk).update(capacity=0))


# --- Clubs ---------------------------------------------------------------------------------------


def test_one_open_membership_per_club():
    club = Club.objects.create(code="CHESS", name="Chess club")
    student = f.student()
    ClubMembership.objects.create(club=club, student=student)
    violates(lambda: ClubMembership.objects.create(club=club, student=student, status=MembershipStatus.APPROVED))


# --- Requests ------------------------------------------------------------------------------------


def test_transfer_category_must_require_transfer_approval():
    violates(
        lambda: RequestCategory.objects.create(
            code="BAD_TRANSFER", name="x", is_transfer=True, requires_approval=True,
            approval_capability="approve_requests",
        )
    )
    violates(
        lambda: RequestCategory.objects.create(
            code="BAD_TRANSFER2", name="x", is_transfer=True, requires_approval=False,
            approval_capability="approve_transfers",
        )
    )


def test_transfer_target_must_match_type():
    student = f.student()
    category = RequestCategory.objects.get(code="TRANSFER")
    req = StudentRequest.objects.create(number="REQ-2026-000001", student=student, category=category,
                                        subject="Move", description="Please")
    common = {"request": req, "from_program": student.program, "from_department": student.program.department,
              "from_campus": "Main Campus", "reason": "x"}
    # PROGRAM transfer without a target program
    violates(lambda: TransferRequest.objects.create(transfer_type=TransferType.PROGRAM, **common))
    # PROGRAM transfer naming a campus instead
    violates(lambda: TransferRequest.objects.create(transfer_type=TransferType.PROGRAM, to_campus="North", **common))
    TransferRequest.objects.create(transfer_type=TransferType.PROGRAM, to_program=f.program(), **common)


def test_request_status_vocabulary_enforced():
    student = f.student()
    req = StudentRequest.objects.create(number="REQ-2026-000002", student=student,
                                        category=RequestCategory.objects.get(code="GENERAL"),
                                        subject="Hi", description="x")
    violates(lambda: StudentRequest.objects.filter(pk=req.pk).update(status="AUTO_APPROVED"))


def test_default_request_categories_are_seeded():
    codes = set(RequestCategory.objects.values_list("code", flat=True))
    assert {"ACADEMIC_QUERY", "TRANSFER", "HOSTEL_REQUEST", "TRANSCRIPT", "OTHER"} <= codes
    assert RequestCategory.objects.get(code="TRANSFER").approval_capability == "approve_transfers"


# --- Announcements -------------------------------------------------------------------------------


def test_announcement_scope_requires_matching_target():
    author = f.user(Role.ADMIN)
    now = timezone.now()
    base = {"title": "t", "body": "b", "author": author, "publish_at": now}
    violates(lambda: Announcement.objects.create(scope=Scope.DEPARTMENT, **base))  # missing department
    violates(lambda: Announcement.objects.create(scope=Scope.UNIVERSITY, department=f.department(), **base))
    violates(lambda: Announcement.objects.create(scope=Scope.UNIVERSITY, expires_at=now - timedelta(days=1), **base))
    Announcement.objects.create(scope=Scope.DEPARTMENT, department=f.department(), **base)


@pytest.mark.postgres
def test_postgres_exclusion_constraint_blocks_overlapping_venue_booking():
    offering_a = f.offering()
    offering_b = f.offering(semester=offering_a.semester)
    venue = Venue.objects.create(code="LH9", name="Lecture hall 9")
    common = {"semester": offering_a.semester, "venue": venue, "day_of_week": 2}
    TimetableEntry.objects.create(offering=offering_a, start_time=f.at(9), end_time=f.at(11), **common)
    violates(lambda: TimetableEntry.objects.create(offering=offering_b, start_time=f.at(10), end_time=f.at(12), **common))
    # Back-to-back is fine (ranges are half-open).
    TimetableEntry.objects.create(offering=offering_b, start_time=f.at(11), end_time=f.at(12), **common)

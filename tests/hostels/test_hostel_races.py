"""Concurrency: two students cannot both get the last bed (spec §32; ARCHITECTURE.md T7). PostgreSQL only."""

import pytest

from apps.core.context import SYSTEM
from apps.hostels import services
from apps.hostels.models import AllocationStatus, HostelAllocation
from tests import factories as f
from tests.academics.test_registration_races import run_concurrently

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def test_last_bed_goes_to_exactly_one_student():
    semester = f.semester(is_current=True)
    f.booking_window(semester)
    bed = f.bed(f.hostel(gender_policy="MIXED"))
    students = [f.student() for _ in range(6)]
    results = run_concurrently([lambda s=s: services.book_bed(s.user, s, bed, semester, SYSTEM) for s in students])
    assert sum(isinstance(r, HostelAllocation) for r in results) == 1
    assert HostelAllocation.objects.filter(bed=bed, status=AllocationStatus.ACTIVE).count() == 1


def test_one_student_cannot_take_two_beds_concurrently():
    semester = f.semester(is_current=True)
    f.booking_window(semester)
    student = f.student()
    beds = [f.bed(f.hostel(gender_policy="MIXED")) for _ in range(4)]
    run_concurrently([lambda b=b: services.book_bed(student.user, student, b, semester, SYSTEM) for b in beds])
    assert HostelAllocation.objects.filter(student=student, status=AllocationStatus.ACTIVE).count() == 1

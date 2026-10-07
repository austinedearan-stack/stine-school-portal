"""Timetable read queries scoped to the actor."""

from __future__ import annotations

from apps.academics.models import RegistrationStatus
from apps.timetable.models import TimetableEntry

_RELATED = ("offering__unit", "venue", "lecturer__user", "student_group")


def entries_for_student(student, semester):
    if semester is None:
        return TimetableEntry.objects.none()
    return (
        TimetableEntry.objects.filter(
            semester=semester,
            offering__registrations__student=student,
            offering__registrations__status=RegistrationStatus.REGISTERED,
        )
        .select_related(*_RELATED)
        .distinct()
        .order_by("day_of_week", "start_time")
    )


def entries_for_lecturer(staff, semester):
    if semester is None or staff is None:
        return TimetableEntry.objects.none()
    return (
        TimetableEntry.objects.filter(semester=semester, offering__lecturer=staff)
        .select_related(*_RELATED)
        .order_by("day_of_week", "start_time")
    )

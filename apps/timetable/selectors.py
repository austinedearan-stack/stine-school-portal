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


def master(semester, *, department=None, venue=None, day=None, group=None):
    """The public master timetable for a semester (visible to every signed-in user)."""
    if semester is None:
        return TimetableEntry.objects.none()
    qs = TimetableEntry.objects.filter(semester=semester).select_related(*_RELATED, "offering__unit__department")
    if department is not None:
        qs = qs.filter(offering__unit__department=department)
    if venue is not None:
        qs = qs.filter(venue=venue)
    if day:
        qs = qs.filter(day_of_week=day)
    if group is not None:
        qs = qs.filter(student_group=group)
    return qs.order_by("day_of_week", "start_time", "venue__code")

"""Read queries for academics, always scoped to the actor (ARCHITECTURE.md §1.3 rule 1)."""

from __future__ import annotations

from apps.academics.models import (
    LIVE_REGISTRATION_STATUSES,
    OfferingStatus,
    RegistrationStatus,
    Semester,
    UnitOffering,
    UnitRegistration,
)


def current_semester() -> Semester | None:
    return Semester.objects.filter(is_current=True).select_related("academic_year").first()


def registrations_for_student(student, semester=None, *, live_only: bool = True):
    qs = UnitRegistration.objects.filter(student=student).select_related(
        "offering__unit", "offering__lecturer__user", "semester"
    )
    if semester is not None:
        qs = qs.filter(semester=semester)
    if live_only:
        qs = qs.filter(status__in=LIVE_REGISTRATION_STATUSES)
    return qs.order_by("offering__unit__code")


def current_registrations(student):
    semester = current_semester()
    if semester is None:
        return UnitRegistration.objects.none()
    return registrations_for_student(student, semester).filter(status=RegistrationStatus.REGISTERED)


def teaching_offerings(staff, semester=None):
    if staff is None:
        return UnitOffering.objects.none()
    qs = UnitOffering.objects.filter(lecturer=staff).exclude(status=OfferingStatus.CANCELLED).select_related(
        "unit", "semester"
    )
    if semester is not None:
        qs = qs.filter(semester=semester)
    return qs.order_by("unit__code", "section")

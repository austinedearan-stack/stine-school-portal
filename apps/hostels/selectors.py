"""Hostel read queries scoped to the actor or to public availability."""

from __future__ import annotations

from django.db.models import Count, Exists, OuterRef, Q

from apps.hostels.models import (
    HOLDING_ALLOCATION_STATUSES,
    ApplicationStatus,
    Bed,
    Hostel,
    HostelAllocation,
    HostelApplication,
    SpaceStatus,
)


def current_allocation(student, semester):
    if semester is None or student is None:
        return None
    return (
        HostelAllocation.objects.filter(student=student, semester=semester, status__in=HOLDING_ALLOCATION_STATUSES)
        .select_related("bed__room__floor__building__hostel")
        .first()
    )


def free_beds(semester, hostel=None):
    """Beds that are in service and not held by anyone this semester (occupancy is derived, D12)."""
    held = HostelAllocation.objects.filter(bed=OuterRef("pk"), semester=semester,
                                           status__in=HOLDING_ALLOCATION_STATUSES)
    qs = Bed.objects.filter(
        status=SpaceStatus.AVAILABLE, room__status=SpaceStatus.AVAILABLE, room__floor__building__is_active=True,
        room__floor__building__hostel__is_active=True,
    ).exclude(Exists(held)).select_related("room__floor__building__hostel")
    if hostel is not None:
        qs = qs.filter(room__floor__building__hostel=hostel)
    return qs.order_by("room__floor__building__name", "room__floor__level", "room__number", "label")


def hostel_catalogue(semester):
    hostels = list(Hostel.objects.filter(is_active=True).order_by("name"))
    if semester is None:
        return [(h, 0, 0) for h in hostels]
    free_counts = dict(
        free_beds(semester).order_by().values("room__floor__building__hostel").annotate(n=Count("pk"))
        .values_list("room__floor__building__hostel", "n"))
    # order_by() clears model ordering, which would otherwise leak into GROUP BY and split the counts.
    totals = dict(Bed.objects.order_by().values("room__floor__building__hostel").annotate(n=Count("pk"))
                  .values_list("room__floor__building__hostel", "n"))
    return [(h, free_counts.get(h.pk, 0), totals.get(h.pk, 0)) for h in hostels]


def open_application(student, semester):
    if student is None or semester is None:
        return None
    return (
        HostelApplication.objects.filter(student=student, semester=semester)
        .exclude(status__in=[ApplicationStatus.CANCELLED, ApplicationStatus.REJECTED])
        .prefetch_related("preferences__hostel").first()
    )


def history(student):
    return HostelAllocation.objects.filter(student=student).select_related(
        "bed__room__floor__building__hostel", "semester").order_by("-created_at")


def applications_for_review(semester, status=None):
    qs = HostelApplication.objects.filter(semester=semester).select_related("student__user", "student__program")
    if status:
        qs = qs.filter(status=status)
    return qs.prefetch_related("preferences__hostel").order_by("created_at")


def allocations_for_semester(semester, status=None):
    qs = HostelAllocation.objects.filter(semester=semester).select_related(
        "student__user", "bed__room__floor__building__hostel")
    if status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(Q(status__in=HOLDING_ALLOCATION_STATUSES))
    return qs.order_by("bed__room__floor__building__hostel__name", "bed__room__number", "bed__label")

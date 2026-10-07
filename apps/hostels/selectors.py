"""Hostel read queries scoped to the actor."""

from __future__ import annotations

from apps.hostels.models import HOLDING_ALLOCATION_STATUSES, HostelAllocation


def current_allocation(student, semester):
    if semester is None or student is None:
        return None
    return (
        HostelAllocation.objects.filter(student=student, semester=semester, status__in=HOLDING_ALLOCATION_STATUSES)
        .select_related("bed__room__floor__building__hostel")
        .first()
    )

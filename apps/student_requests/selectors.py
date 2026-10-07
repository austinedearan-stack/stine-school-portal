"""Request read queries scoped to the actor."""

from __future__ import annotations

from apps.student_requests.models import TERMINAL_REQUEST_STATUSES, RequestStatus, StudentRequest


def open_requests_for_student(student):
    if student is None:
        return StudentRequest.objects.none()
    return (
        StudentRequest.objects.filter(student=student)
        .exclude(status__in=[*TERMINAL_REQUEST_STATUSES, RequestStatus.RESOLVED])
        .select_related("category")
        .order_by("-created_at")
    )


def assigned_open_requests(staff):
    if staff is None:
        return StudentRequest.objects.none()
    return (
        StudentRequest.objects.filter(assigned_to=staff)
        .exclude(status__in=[*TERMINAL_REQUEST_STATUSES, RequestStatus.RESOLVED])
        .select_related("category", "student__user")
        .order_by("-created_at")
    )

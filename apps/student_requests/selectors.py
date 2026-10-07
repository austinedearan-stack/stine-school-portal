"""Request read queries scoped to the actor (mirror of the can_view_request policy, as a queryset)."""

from __future__ import annotations

from django.db.models import Q

from apps.core.authz import has_capability
from apps.core.capabilities import Role
from apps.student_requests.models import TERMINAL_REQUEST_STATUSES, RequestStatus, StudentRequest

_RELATED = ("category", "student__user", "student__program", "assigned_to__user", "department")


def open_requests_for_student(student):
    if student is None:
        return StudentRequest.objects.none()
    return (
        StudentRequest.objects.filter(student=student)
        .exclude(status__in=[*TERMINAL_REQUEST_STATUSES, RequestStatus.RESOLVED])
        .select_related("category")
        .order_by("-created_at")
    )


def requests_for_student(student):
    return StudentRequest.objects.filter(student=student).select_related("category").order_by("-created_at")


def assigned_open_requests(staff):
    if staff is None:
        return StudentRequest.objects.none()
    return (
        StudentRequest.objects.filter(assigned_to=staff)
        .exclude(status__in=[*TERMINAL_REQUEST_STATUSES, RequestStatus.RESOLVED])
        .select_related("category", "student__user")
        .order_by("-created_at")
    )


def queue_scope(actor) -> Q | None:
    """Q selecting every request the actor may see as staff, or None if they may see none."""
    if has_capability(actor, "review_all_requests"):
        return Q()
    staff = getattr(actor, "staff_profile", None)
    parts = []
    if has_capability(actor, "review_requests") and staff is not None:
        parts.append(Q(assigned_to=staff) | Q(department=staff.department))
    for cap in ("approve_requests", "approve_transfers"):
        if not has_capability(actor, cap):
            continue
        approvable = Q(category__requires_approval=True, category__approval_capability=cap)
        if actor.role in Role.ADMINS:
            parts.append(approvable)
        elif staff is not None:
            parts.append(approvable & (Q(department=staff.department) | Q(assigned_to=staff)))
    if has_capability(actor, "execute_transfers"):
        parts.append(Q(category__is_transfer=True))
    if not parts:
        return None
    combined = parts[0]
    for part in parts[1:]:
        combined |= part
    return combined


def queue(actor, *, status: str = "", category=None, mine: bool = False, query: str = ""):
    scope = queue_scope(actor)
    if scope is None:
        return StudentRequest.objects.none()
    qs = StudentRequest.objects.filter(scope).exclude(student__user=actor).select_related(*_RELATED)
    if status == "open":
        qs = qs.exclude(status__in=TERMINAL_REQUEST_STATUSES)
    elif status:
        qs = qs.filter(status=status)
    if category is not None:
        qs = qs.filter(category=category)
    if mine:
        staff = getattr(actor, "staff_profile", None)
        qs = qs.filter(assigned_to=staff) if staff else qs.none()
    if query:
        qs = qs.filter(Q(number__icontains=query) | Q(subject__icontains=query) |
                       Q(student__student_number__icontains=query))
    return qs.order_by("-created_at")


def thread(req, *, include_internal: bool):
    messages = req.messages.select_related("author").order_by("created_at")
    if not include_internal:
        messages = messages.filter(visibility="PUBLIC")
    return messages


def attachments(req, *, include_internal: bool):
    qs = req.attachments.select_related("file", "uploaded_by").order_by("created_at")
    if not include_internal:
        qs = qs.filter(visibility="PUBLIC")
    return qs

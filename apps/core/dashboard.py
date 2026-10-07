"""Dashboard context per role. Identity comes only from the session user; no query parameter can
change whose data is shown."""

from __future__ import annotations

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.academics import selectors as academics
from apps.academics.models import RegistrationStatus
from apps.core.authz import capabilities_of, has_capability
from apps.core.capabilities import Role
from apps.hostels import selectors as hostels
from apps.notifications import selectors as notifications
from apps.student_requests import selectors as requests
from apps.timetable import selectors as timetable


def _week(entries):
    days: dict[int, list] = {}
    for entry in entries:
        days.setdefault(entry.day_of_week, []).append(entry)
    return [(entries_for_day[0].get_day_of_week_display(), entries_for_day) for _, entries_for_day in sorted(days.items())]


def dashboard_context(user) -> dict:
    semester = academics.current_semester()
    context = {
        "semester": semester,
        "today": timezone.localdate(),
        "notifications": notifications.notifications_for(user)[:5],
        "announcements": notifications.visible_announcements(user)[:3],
    }
    if user.role == Role.STUDENT:
        student = getattr(user, "student_profile", None)
        context["student"] = student
        if student is not None:
            registrations = list(academics.current_registrations(student))
            context.update(
                registrations=registrations,
                credit_total=sum(r.offering.unit.credit_hours for r in registrations),
                week=_week(timetable.entries_for_student(student, semester)),
                allocation=hostels.current_allocation(student, semester),
                open_requests=requests.open_requests_for_student(student)[:5],
            )
    elif user.role == Role.STAFF:
        staff = getattr(user, "staff_profile", None)
        offerings = academics.teaching_offerings(staff, semester).annotate(
            enrolled=Count("registrations", filter=Q(registrations__status=RegistrationStatus.REGISTERED))
        )
        context.update(
            staff=staff,
            offerings=offerings,
            week=_week(timetable.entries_for_lecturer(staff, semester)),
            assigned_requests=requests.assigned_open_requests(staff)[:5],
        )
    else:
        context["capabilities"] = capabilities_of(user)
        if has_capability(user, "view_statistics"):
            context["stats"] = admin_statistics(semester)
    return context


def admin_statistics(semester) -> dict:
    from apps.accounts.models import StaffProfile, StudentProfile
    from apps.hostels.models import HOLDING_ALLOCATION_STATUSES, Bed, HostelAllocation
    from apps.student_requests.models import TERMINAL_REQUEST_STATUSES, StudentRequest

    stats = {
        "students": StudentProfile.objects.filter(user__is_active=True).count(),
        "staff": StaffProfile.objects.filter(user__is_active=True).count(),
        "open_requests": StudentRequest.objects.exclude(status__in=TERMINAL_REQUEST_STATUSES).count(),
        "beds": Bed.objects.count(),
        "beds_held": 0,
        "registrations": 0,
        "credits": 0,
    }
    if semester is not None:
        stats["beds_held"] = HostelAllocation.objects.filter(
            semester=semester, status__in=HOLDING_ALLOCATION_STATUSES).count()
        regs = semester.registrations.filter(status=RegistrationStatus.REGISTERED)
        stats["registrations"] = regs.count()
        stats["credits"] = regs.aggregate(total=Sum("unit__credit_hours"))["total"] or 0
    return stats

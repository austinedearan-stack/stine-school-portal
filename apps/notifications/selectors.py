"""Notification and announcement read queries.

Announcement visibility (fix for audit Z-1/Z-2) is decided in ONE place, ``audience_q``: a published,
current announcement is visible to a user only if the user matches BOTH the role audience and the scope
(faculty / department / program / offering / club / named recipients). Authors always see their own.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from apps.academics.models import RegistrationStatus
from apps.core.capabilities import Role
from apps.notifications.models import Announcement, AnnouncementStatus, Audience, Notification, Scope


def notifications_for(user):
    return Notification.objects.filter(recipient=user).order_by("-created_at")


def unread_count(user) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


def _role_audience(user) -> list[str]:
    if user.role == Role.STUDENT:
        return [Audience.EVERYONE, Audience.STUDENTS]
    return [Audience.EVERYONE, Audience.STAFF]


def audience_q(user) -> Q:
    """Q over Announcement selecting the scopes ``user`` belongs to (role audience applied separately)."""
    q = Q(scope=Scope.UNIVERSITY) | Q(scope=Scope.INDIVIDUAL, recipients=user)
    student = getattr(user, "student_profile", None) if user.role == Role.STUDENT else None
    staff = getattr(user, "staff_profile", None) if user.role != Role.STUDENT else None
    if student is not None:
        program = student.program
        q |= Q(scope=Scope.PROGRAM, program_id=program.pk)
        q |= Q(scope=Scope.DEPARTMENT, department_id=program.department_id)
        q |= Q(scope=Scope.FACULTY, faculty_id=program.department.faculty_id)
        q |= Q(scope=Scope.OFFERING, offering__registrations__student=student,
               offering__registrations__status=RegistrationStatus.REGISTERED)
        q |= Q(scope=Scope.CLUB, club__memberships__student=student, club__memberships__status="APPROVED")
    if staff is not None:
        department = staff.department
        q |= Q(scope=Scope.DEPARTMENT, department_id=department.pk)
        q |= Q(scope=Scope.FACULTY, faculty_id=department.faculty_id)
        q |= Q(scope=Scope.PROGRAM, program__department_id=department.pk)
        q |= Q(scope=Scope.OFFERING, offering__lecturer=staff)
        q |= Q(scope=Scope.CLUB, club__advisor=staff)
    return q


def live() -> Q:
    now = timezone.now()
    return Q(status=AnnouncementStatus.PUBLISHED, publish_at__lte=now) & (Q(expires_at__isnull=True) |
                                                                          Q(expires_at__gt=now))


def visible_announcements(user):
    targeted = Announcement.objects.filter(live(), audience_q(user), audience_roles__in=_role_audience(user))
    return (
        Announcement.objects.filter(Q(pk__in=targeted.values("pk")) | Q(author=user))
        .select_related("author", "department", "faculty", "program", "offering__unit", "club")
        .order_by("-publish_at")
    )


def can_see(user, announcement) -> bool:
    return announcement.author_id == user.pk or visible_announcements(user).filter(pk=announcement.pk).exists()


def audience_users(announcement):
    """Every active user who should receive the announcement (for notifications)."""
    User = get_user_model()
    scope = announcement.scope
    if scope == Scope.INDIVIDUAL:
        users = announcement.recipients.all()
    else:
        students = Q(role=Role.STUDENT)
        staff = ~Q(role=Role.STUDENT)
        if scope == Scope.UNIVERSITY:
            match = Q()
        elif scope == Scope.FACULTY:
            match = (students & Q(student_profile__program__department__faculty=announcement.faculty_id)) | (
                staff & Q(staff_profile__department__faculty=announcement.faculty_id))
        elif scope == Scope.DEPARTMENT:
            match = (students & Q(student_profile__program__department=announcement.department_id)) | (
                staff & Q(staff_profile__department=announcement.department_id))
        elif scope == Scope.PROGRAM:
            match = (students & Q(student_profile__program=announcement.program_id)) | (
                staff & Q(staff_profile__department=announcement.program.department_id))
        elif scope == Scope.OFFERING:
            match = (students & Q(student_profile__registrations__offering=announcement.offering_id,
                                  student_profile__registrations__status=RegistrationStatus.REGISTERED)) | (
                staff & Q(staff_profile__offerings=announcement.offering_id))
        else:  # CLUB
            match = (students & Q(student_profile__club_memberships__club=announcement.club_id,
                                  student_profile__club_memberships__status="APPROVED")) | (
                staff & Q(staff_profile__advised_clubs=announcement.club_id))
        users = User.objects.filter(match)
    if announcement.audience_roles == Audience.STUDENTS:
        users = users.filter(role=Role.STUDENT)
    elif announcement.audience_roles == Audience.STAFF:
        users = users.exclude(role=Role.STUDENT)
    return users.filter(is_active=True).exclude(pk=announcement.author_id).distinct()

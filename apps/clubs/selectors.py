"""Club read queries."""

from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone

from apps.clubs.models import Club, ClubEvent, ClubMembership, EventVisibility, MembershipStatus


def directory(*, query: str = "", kind: str = "", category: str = ""):
    qs = Club.objects.filter(is_active=True).annotate(
        member_count=Count("memberships", filter=Q(memberships__status=MembershipStatus.APPROVED)))
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(code__icontains=query))
    if kind:
        qs = qs.filter(kind=kind)
    if category:
        qs = qs.filter(category__iexact=category)
    return qs.select_related("advisor__user").order_by("name")


def categories() -> list[str]:
    return sorted(set(Club.objects.filter(is_active=True).exclude(category="").values_list("category", flat=True)))


def membership_of(student, club):
    if student is None:
        return None
    return club.memberships.filter(student=student,
                                   status__in=[MembershipStatus.PENDING, MembershipStatus.APPROVED]).first()


def visible_events(club, *, include_members_only: bool):
    qs = club.events.filter(ends_at__gte=timezone.now()).order_by("starts_at")
    if not include_members_only:
        qs = qs.filter(visibility=EventVisibility.PUBLIC)
    return qs


def my_memberships(student):
    return ClubMembership.objects.filter(student=student).exclude(
        status__in=[MembershipStatus.REJECTED]).select_related("club").order_by("club__name")


def members(club):
    return club.memberships.filter(status=MembershipStatus.APPROVED).select_related(
        "student__user", "student__program").order_by("-can_manage_members", "position", "student__user__last_name")


def pending(club):
    return club.memberships.filter(status=MembershipStatus.PENDING).select_related(
        "student__user", "student__program").order_by("created_at")


def events_for_management(club):
    return ClubEvent.objects.filter(club=club).order_by("-starts_at")

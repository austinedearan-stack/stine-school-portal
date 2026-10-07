"""Club membership and administration (ARCHITECTURE.md §5.3 club rows; audit Z-7).

* Joining creates a PENDING request unless the club explicitly does not require approval.
* Membership decisions: the advisor, manage_clubs holders, or an officer with can_manage_members -
  never on one's own membership; officers decide on ordinary members only.
* Officer appointment (position, can_manage_members): the advisor or manage_clubs holders only.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import StudentProfile
from apps.clubs.models import Club, ClubEvent, ClubMembership, MembershipStatus, Position
from apps.core.audit import diff, record_audit_event
from apps.core.authz import authorize
from apps.core.context import RequestContext
from apps.notifications.services import notify, notify_many

OPEN_STATUSES = (MembershipStatus.PENDING, MembershipStatus.APPROVED)


class ClubError(ValidationError):
    pass


def _reviewers(club) -> list:
    users = []
    if club.advisor_id:
        users.append(club.advisor.user)
    users += [m.student.user for m in club.memberships.filter(status=MembershipStatus.APPROVED,
                                                              can_manage_members=True).select_related("student__user")]
    return users


def request_membership(actor, student: StudentProfile, club: Club, ctx: RequestContext) -> ClubMembership:
    authorize(actor, "can_act_for_student", student, ctx=ctx)
    if not club.is_active:
        raise ClubError("This club is not accepting members.")
    status = MembershipStatus.PENDING if club.requires_approval else MembershipStatus.APPROVED
    try:
        with transaction.atomic():
            membership = ClubMembership.objects.create(club=club, student=student, status=status,
                                                       decided_at=None if club.requires_approval else timezone.now())
    except IntegrityError as exc:
        raise ClubError("You already have a membership or a pending request for this club.") from exc
    record_audit_event(actor, "CLUB.JOIN_REQUESTED" if club.requires_approval else "CLUB.JOINED", membership, ctx=ctx)
    if club.requires_approval:
        notify_many(_reviewers(club), "CLUB", f"New membership request for {club.name}",
                    f"{student.user.full_name} asked to join.", route_name="clubs:members",
                    route_kwargs={"club_id": str(club.pk)})
    return membership


def leave(actor, membership: ClubMembership, ctx: RequestContext) -> ClubMembership:
    authorize(actor, "can_act_for_student", membership.student, ctx=ctx)
    with transaction.atomic():
        membership = ClubMembership.objects.select_for_update().get(pk=membership.pk)
        if membership.status not in OPEN_STATUSES:
            raise ClubError("You are not a member of this club.")
        membership.status, membership.can_manage_members, membership.position = (
            MembershipStatus.LEFT, False, Position.MEMBER)
        membership.save(update_fields=["status", "can_manage_members", "position", "updated_at"])
        record_audit_event(actor, "CLUB.LEFT", membership, ctx=ctx)
    return membership


def decide(actor, membership: ClubMembership, approve: bool, ctx: RequestContext) -> ClubMembership:
    with transaction.atomic():
        membership = ClubMembership.objects.select_for_update(of=("self",)).select_related(
            "club__advisor", "student__user").get(pk=membership.pk)
        authorize(actor, "can_review_membership", membership, ctx=ctx)
        if membership.status != MembershipStatus.PENDING:
            raise ClubError("This request has already been decided.")
        membership.status = MembershipStatus.APPROVED if approve else MembershipStatus.REJECTED
        membership.decided_by, membership.decided_at = actor, timezone.now()
        membership.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        record_audit_event(actor, "CLUB.MEMBERSHIP_APPROVED" if approve else "CLUB.MEMBERSHIP_REJECTED", membership,
                           ctx=ctx)
        notify(membership.student.user, "CLUB", f"{membership.club.name}: request {'approved' if approve else 'declined'}",
               "Welcome to the club!" if approve else "Your membership request was not accepted.",
               route_name="clubs:detail", route_kwargs={"club_id": str(membership.club_id)})
    return membership


def remove(actor, membership: ClubMembership, ctx: RequestContext) -> ClubMembership:
    with transaction.atomic():
        membership = ClubMembership.objects.select_for_update(of=("self",)).select_related(
            "club__advisor", "student__user").get(pk=membership.pk)
        authorize(actor, "can_review_membership", membership, ctx=ctx)
        if membership.status != MembershipStatus.APPROVED:
            raise ClubError("Only current members can be removed.")
        membership.status, membership.can_manage_members, membership.position = (
            MembershipStatus.REMOVED, False, Position.MEMBER)
        membership.decided_by, membership.decided_at = actor, timezone.now()
        membership.save(update_fields=["status", "can_manage_members", "position", "decided_by", "decided_at",
                                       "updated_at"])
        record_audit_event(actor, "CLUB.MEMBER_REMOVED", membership, ctx=ctx)
        notify(membership.student.user, "CLUB", f"Removed from {membership.club.name}",
               "Your club membership was ended by the club.")
    return membership


def appoint(actor, membership: ClubMembership, position: str, can_manage_members: bool,
            ctx: RequestContext) -> ClubMembership:
    """Set a member's office. Advisor or manage_clubs only; never by a student, never on one's own row."""
    if position not in Position.values:
        raise ClubError("Unknown position.")
    with transaction.atomic():
        membership = ClubMembership.objects.select_for_update(of=("self",)).select_related(
            "club__advisor", "student__user").get(pk=membership.pk)
        authorize(actor, "can_manage_club", membership.club, ctx=ctx)
        if membership.student.user_id == actor.pk:
            raise ClubError("You cannot change your own club position.")
        if membership.status != MembershipStatus.APPROVED:
            raise ClubError("Only approved members can hold an office.")
        before = {"position": membership.position, "can_manage_members": membership.can_manage_members}
        membership.position, membership.can_manage_members = position, can_manage_members
        membership.save(update_fields=["position", "can_manage_members", "updated_at"])
        record_audit_event(actor, "CLUB.OFFICER_APPOINTED", membership, ctx=ctx,
                           changes=diff(before, {"position": position, "can_manage_members": can_manage_members}))
        notify(membership.student.user, "CLUB", f"{membership.club.name}: your role changed",
               f"You are now {membership.get_position_display()}.")
    return membership


def save_club(actor, club: Club, ctx: RequestContext, *, changed_fields=()) -> Club:
    creating = club._state.adding
    authorize(actor, "can_manage_club", None if creating else club, ctx=ctx)
    if not creating and "advisor" in changed_fields:
        authorize(actor, "can_manage_clubs_admin", club, ctx=ctx)  # an advisor cannot reassign the advisor role
    club.full_clean()
    club.save()
    record_audit_event(actor, "CLUB.CREATED" if creating else "CLUB.UPDATED", club, ctx=ctx,
                       changes={"fields": sorted(changed_fields)} if changed_fields else None)
    return club


def save_event(actor, event: ClubEvent, ctx: RequestContext) -> ClubEvent:
    authorize(actor, "can_manage_club_events", event.club, ctx=ctx)
    creating = event._state.adding
    if creating:
        event.created_by = actor
    event.full_clean()
    event.save()
    record_audit_event(actor, "CLUB.EVENT_CREATED" if creating else "CLUB.EVENT_UPDATED", event, ctx=ctx)
    if creating:
        members = [m.student.user for m in event.club.memberships.filter(status=MembershipStatus.APPROVED)
                   .select_related("student__user")]
        notify_many(members, "CLUB", f"{event.club.name}: {event.title}",
                    f"{event.starts_at:%d %b %H:%M} at {event.location or 'TBA'}.",
                    route_name="clubs:detail", route_kwargs={"club_id": str(event.club_id)})
    return event

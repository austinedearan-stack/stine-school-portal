"""Clubs: membership approval, officer rules, privacy and events (Phase 8; spec §10; audit Z-7)."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.clubs import services
from apps.clubs.models import Club, ClubEvent, ClubMembership, MembershipStatus, Position
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db
ClubError = services.ClubError


@pytest.fixture
def advisor():
    return f.staff()


@pytest.fixture
def club(advisor):
    return Club.objects.create(code=f"C{f.n()}", name=f"Club {f.n()}", advisor=advisor)


def join(student, club):
    return services.request_membership(student.user, student, club, SYSTEM)


def approved_member(club, **kw):
    s = f.student()
    return ClubMembership.objects.create(club=club, student=s, status=MembershipStatus.APPROVED, **kw)


def test_joining_requires_approval_by_default(club):
    membership = join(f.student(), club)
    assert membership.status == MembershipStatus.PENDING  # audit Z-7: no auto-approval
    assert club.advisor.user.notifications.filter(kind="CLUB").exists()


def test_open_club_admits_immediately(advisor):
    open_club = Club.objects.create(code="OPEN", name="Open", advisor=advisor, requires_approval=False)
    assert join(f.student(), open_club).status == MembershipStatus.APPROVED


def test_cannot_request_twice_but_can_rejoin_after_leaving(club):
    s = f.student()
    membership = join(s, club)
    with pytest.raises(ClubError):
        join(s, club)
    services.leave(s.user, membership, SYSTEM)
    join(s, club)


def test_advisor_approves(club, advisor):
    membership = join(f.student(), club)
    services.decide(advisor.user, membership, True, SYSTEM)
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.APPROVED and membership.decided_by == advisor.user


def test_officer_with_permission_approves_ordinary_members(club):
    officer = approved_member(club, position=Position.SECRETARY, can_manage_members=True)
    membership = join(f.student(), club)
    services.decide(officer.student.user, membership, True, SYSTEM)


def test_officer_without_permission_cannot_approve(club):
    chair = approved_member(club, position=Position.CHAIR)
    with pytest.raises(PermissionDenied):
        services.decide(chair.student.user, join(f.student(), club), True, SYSTEM)


def test_officers_never_act_on_their_own_membership(club):
    officer = approved_member(club, can_manage_members=True)
    with pytest.raises(PermissionDenied):
        services.remove(officer.student.user, officer, SYSTEM)


def test_officer_cannot_remove_another_officer(club):
    officer = approved_member(club, can_manage_members=True)
    other_officer = approved_member(club, can_manage_members=True, position=Position.TREASURER)
    with pytest.raises(PermissionDenied):
        services.remove(officer.student.user, other_officer, SYSTEM)


def test_only_advisor_or_office_appoints_officers(club, advisor):
    officer = approved_member(club, can_manage_members=True)
    member = approved_member(club)
    with pytest.raises(PermissionDenied):
        services.appoint(officer.student.user, member, Position.CHAIR, True, SYSTEM)
    with pytest.raises(PermissionDenied):
        services.appoint(member.student.user, member, Position.CHAIR, True, SYSTEM)  # self-promotion
    services.appoint(advisor.user, member, Position.CHAIR, True, SYSTEM)
    member.refresh_from_db()
    assert member.position == Position.CHAIR and member.can_manage_members
    student_affairs = f.user(Role.ADMIN, groups=["Student Affairs"])
    services.appoint(student_affairs, member, Position.MEMBER, False, SYSTEM)


def test_leaving_drops_office(club):
    officer = approved_member(club, position=Position.CHAIR, can_manage_members=True)
    services.leave(officer.student.user, officer, SYSTEM)
    officer.refresh_from_db()
    assert officer.status == MembershipStatus.LEFT and not officer.can_manage_members


def test_advisor_cannot_reassign_the_advisor(club, advisor):
    club.advisor = f.staff()
    with pytest.raises(PermissionDenied):
        services.save_club(advisor.user, club, SYSTEM, changed_fields=["advisor"])


def test_events_notify_members_and_respect_visibility(client, club, advisor):
    member = approved_member(club)
    now = timezone.now()
    services.save_event(advisor.user, ClubEvent(club=club, title="Secret meetup", starts_at=now + timedelta(days=1),
                                                ends_at=now + timedelta(days=1, hours=2), visibility="MEMBERS"), SYSTEM)
    assert member.student.user.notifications.filter(kind="CLUB").exists()
    outsider = f.student()
    login(client, outsider.user)
    assert b"Secret meetup" not in client.get(reverse("clubs:detail", args=[club.pk])).content
    login(client, member.student.user)
    assert b"Secret meetup" in client.get(reverse("clubs:detail", args=[club.pk])).content


# --- Views ----------------------------------------------------------------------------------------


def test_join_through_ui_and_search(client, club):
    s = f.student()
    login(client, s.user)
    assert club.name.encode() in client.get(reverse("clubs:directory"), {"q": club.name}).content
    client.post(reverse("clubs:join", args=[club.pk]))
    assert ClubMembership.objects.filter(club=club, student=s, status=MembershipStatus.PENDING).exists()


def test_member_list_is_private(client, club):
    approved_member(club)
    login(client, f.student().user)
    assert client.get(reverse("clubs:members", args=[club.pk])).status_code == 404


def test_outsider_cannot_decide_by_guessing_ids(client, club):
    membership = join(f.student(), club)
    login(client, f.student().user)
    assert client.post(reverse("clubs:decide", args=[membership.pk]), {"decision": "approve"}).status_code == 404
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.PENDING


def test_only_manage_clubs_creates_clubs(client):
    login(client, f.staff().user)
    assert client.get(reverse("clubs:create")).status_code == 403
    office = f.user(Role.ADMIN, groups=["Student Affairs"])
    enrol(office)
    login(client, office)
    assert client.get(reverse("clubs:create")).status_code == 200

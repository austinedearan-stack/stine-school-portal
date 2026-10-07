"""Notifications and announcements: audience targeting, publishing scope, read state, outbox
(Phase 11; spec §14–15; audit Z-1, Z-2, Z-10)."""

from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import UnitRegistration
from apps.clubs.models import Club, ClubMembership, MembershipStatus
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.notifications import selectors, services
from apps.notifications.models import Announcement, AnnouncementStatus, Notification, OutboundEmail, Scope
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


def comms_admin():
    user = f.user(Role.ADMIN, groups=["Communications"])
    enrol(user)
    return user


def publish(author, **fields):
    recipients = fields.pop("recipients", None)
    fields.setdefault("publish_at", timezone.now() - timedelta(minutes=1))
    return services.save_announcement(author, Announcement(title=fields.pop("title", "Notice"), body="Body", **fields),
                                      SYSTEM, recipients=recipients, publish=True)


# --- Audience (Z-1, Z-2) --------------------------------------------------------------------------


def test_department_staff_only_announcement_does_not_leak_to_students():
    department = f.department()
    student = f.student(program=f.program(department=department))
    colleague = f.staff(department=department)
    announcement = publish(comms_admin(), audience_roles="STAFF", scope=Scope.DEPARTMENT, department=department)
    assert not selectors.can_see(student.user, announcement)  # Z-2
    assert selectors.can_see(colleague.user, announcement)
    assert not selectors.can_see(f.staff().user, announcement)  # staff of another department


def test_detail_page_enforces_audience(client):
    department = f.department()
    announcement = publish(comms_admin(), audience_roles="EVERYONE", scope=Scope.DEPARTMENT, department=department)
    login(client, f.student().user)  # student of another department
    assert client.get(reverse("notifications:announcement", args=[announcement.pk])).status_code == 404  # Z-1
    login(client, f.student(program=f.program(department=department)).user)
    assert client.get(reverse("notifications:announcement", args=[announcement.pk])).status_code == 200


def test_offering_and_club_and_individual_scopes():
    lecturer = f.staff()
    offering = f.offering(lecturer=lecturer)
    enrolled, other = f.student(), f.student()
    UnitRegistration.objects.create(student=enrolled, offering=offering, unit=offering.unit, semester=offering.semester)
    a = publish(comms_admin(), scope=Scope.OFFERING, offering=offering)
    assert selectors.can_see(enrolled.user, a) and selectors.can_see(lecturer.user, a)
    assert not selectors.can_see(other.user, a)

    club = Club.objects.create(code="CL", name="Club", advisor=f.staff())
    member = f.student()
    ClubMembership.objects.create(club=club, student=member, status=MembershipStatus.APPROVED)
    pending = f.student()
    ClubMembership.objects.create(club=club, student=pending)
    c = publish(comms_admin(), scope=Scope.CLUB, club=club)
    assert selectors.can_see(member.user, c) and not selectors.can_see(pending.user, c)

    chosen = f.student()
    i = publish(comms_admin(), scope=Scope.INDIVIDUAL, recipients=[chosen.user])
    assert selectors.can_see(chosen.user, i) and not selectors.can_see(other.user, i)


def test_drafts_future_and_expired_items_are_hidden():
    student = f.student()
    author = comms_admin()
    draft = services.save_announcement(author, Announcement(title="d", body="b", publish_at=timezone.now()), SYSTEM)
    future = publish(author, publish_at=timezone.now() + timedelta(days=1))
    expired = publish(author, publish_at=timezone.now() - timedelta(days=2), expires_at=timezone.now() - timedelta(days=1))
    for item in (draft, future, expired):
        assert not selectors.can_see(student.user, item)
    assert selectors.can_see(author, draft)  # the author always sees their own


def test_publishing_notifies_exactly_the_audience_once():
    department = f.department()
    inside = [f.student(program=f.program(department=department)) for _ in range(3)]
    outside = f.student()
    announcement = publish(comms_admin(), audience_roles="STUDENTS", scope=Scope.DEPARTMENT, department=department)
    notified = set(Notification.objects.filter(kind="ANNOUNCEMENT").values_list("recipient_id", flat=True))
    assert notified == {s.user_id for s in inside} and outside.user_id not in notified
    assert services.deliver(announcement) == 0  # idempotent


def test_future_announcements_are_delivered_by_the_scheduled_job():
    student = f.student()
    announcement = publish(comms_admin(), publish_at=timezone.now() + timedelta(hours=1))
    assert not Notification.objects.filter(recipient=student.user).exists()
    Announcement.objects.filter(pk=announcement.pk).update(publish_at=timezone.now() - timedelta(minutes=1))
    call_command("publish_due_announcements")
    assert Notification.objects.filter(recipient=student.user, kind="ANNOUNCEMENT").exists()


# --- Publishing scope -----------------------------------------------------------------------------


def test_staff_publisher_is_limited_to_own_department_and_offerings():
    staff = f.staff(groups=["Communications"])
    with pytest.raises(PermissionDenied):
        publish(staff.user, scope=Scope.UNIVERSITY)
    with pytest.raises(PermissionDenied):
        publish(staff.user, scope=Scope.DEPARTMENT, department=f.department())
    publish(staff.user, scope=Scope.DEPARTMENT, department=staff.department)
    with pytest.raises(PermissionDenied):
        publish(staff.user, scope=Scope.OFFERING, offering=f.offering())
    publish(staff.user, scope=Scope.OFFERING, offering=f.offering(lecturer=staff))


def test_staff_individual_messages_only_to_own_students():
    staff = f.staff(groups=["Communications"])
    offering = f.offering(lecturer=staff)
    mine, stranger = f.student(), f.student()
    UnitRegistration.objects.create(student=mine, offering=offering, unit=offering.unit, semester=offering.semester)
    publish(staff.user, scope=Scope.INDIVIDUAL, recipients=[mine.user])
    with pytest.raises(PermissionDenied):
        publish(staff.user, scope=Scope.INDIVIDUAL, recipients=[mine.user, stranger.user])


def test_publishing_needs_the_capability(client):
    with pytest.raises(PermissionDenied):
        publish(f.staff().user, scope=Scope.UNIVERSITY)
    login(client, f.student().user)
    assert client.get(reverse("notifications:announcement_create")).status_code == 403


def test_only_author_or_admin_publisher_can_withdraw():
    author = f.staff(groups=["Communications"])
    announcement = publish(author.user, scope=Scope.DEPARTMENT, department=author.department)
    with pytest.raises(PermissionDenied):
        services.withdraw(f.staff(groups=["Communications"]).user, announcement, SYSTEM)
    services.withdraw(comms_admin(), announcement, SYSTEM)
    announcement.refresh_from_db()
    assert announcement.status == AnnouncementStatus.WITHDRAWN


# --- Inbox (Z-10) ---------------------------------------------------------------------------------


def test_read_state_changes_need_post_and_only_touch_own_notifications(client):
    me, other = f.student(), f.student()
    mine = services.notify(me.user, "SYSTEM", "Mine", route_name="core:dashboard")
    theirs = services.notify(other.user, "SYSTEM", "Theirs")
    login(client, me.user)
    assert client.get(reverse("notifications:open", args=[mine.pk])).status_code == 405
    response = client.post(reverse("notifications:open", args=[mine.pk]))
    assert response.status_code == 302 and response.url == reverse("core:dashboard")
    assert client.post(reverse("notifications:open", args=[theirs.pk])).status_code == 404
    theirs.refresh_from_db()
    assert theirs.read_at is None
    mine.refresh_from_db()
    assert mine.read_at is not None


def test_notifications_cannot_link_off_site():
    with pytest.raises(ValueError):
        services.notify(f.user(), "SYSTEM", "x", route_name="https://evil.example.com")
    rogue = Notification.objects.create(recipient=f.user(), kind="SYSTEM", title="x", route_name="admin:index")
    assert services.link_for(rogue) is None


def test_inbox_shows_only_own_notifications(client):
    me, other = f.student(), f.student()
    services.notify(other.user, "SYSTEM", "Somebody else's secret")
    login(client, me.user)
    assert b"Somebody else" not in client.get(reverse("notifications:list")).content


# --- Email outbox ---------------------------------------------------------------------------------


def test_email_outbox_is_opt_in_and_sent_by_the_job(settings):
    user = f.user()
    services.notify(user, "SYSTEM", "No email")
    assert not OutboundEmail.objects.exists()
    settings.NOTIFICATION_EMAIL_KINDS = ["REQUEST_STATUS"]
    services.notify(user, "REQUEST_STATUS", "Your request moved", "Now under review")
    assert OutboundEmail.objects.count() == 1 and not mail.outbox
    call_command("send_outbox")
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [user.email]
    assert OutboundEmail.objects.get().sent_at is not None

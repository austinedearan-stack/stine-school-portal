"""Hostel booking, applications, offers and accommodation-office actions (Phase 7; spec §9)."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.hostels import selectors, services
from apps.hostels.models import AllocationStatus, ApplicationStatus, HostelAllocation, SpaceStatus
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db
HostelError = services.HostelError


@pytest.fixture
def semester():
    s = f.semester(is_current=True)
    f.booking_window(s)
    return s


@pytest.fixture
def office():
    user = f.user(Role.ADMIN, groups=["Accommodation Office"])
    enrol(user)
    return user


def student(gender="FEMALE"):
    return f.student(gender=gender)


# --- Direct booking -------------------------------------------------------------------------------


def test_student_books_a_free_bed(semester):
    s = student()
    bed = f.bed()
    allocation = services.book_bed(s.user, s, bed, semester, SYSTEM)
    assert allocation.status == AllocationStatus.ACTIVE and allocation.allocated_by is None
    assert bed not in selectors.free_beds(semester)


def test_a_held_bed_cannot_be_booked_again(semester):
    bed = f.bed()
    first = student()
    services.book_bed(first.user, first, bed, semester, SYSTEM)
    second = student()
    with pytest.raises(HostelError, match="no longer available"):
        services.book_bed(second.user, second, bed, semester, SYSTEM)


def test_one_bed_per_student_per_semester(semester):
    s = student()
    services.book_bed(s.user, s, f.bed(), semester, SYSTEM)
    with pytest.raises(HostelError, match="already hold"):
        services.book_bed(s.user, s, f.bed(), semester, SYSTEM)


def test_gender_policy_is_enforced(semester):
    s = student("MALE")
    with pytest.raises(HostelError, match="hostel"):
        services.book_bed(s.user, s, f.bed(f.hostel(gender_policy="FEMALE")), semester, SYSTEM)
    unknown = student("")
    with pytest.raises(HostelError):
        services.book_bed(unknown.user, unknown, f.bed(f.hostel(gender_policy="MALE")), semester, SYSTEM)
    services.book_bed(unknown.user, unknown, f.bed(f.hostel(gender_policy="MIXED")), semester, SYSTEM)


def test_beds_under_maintenance_are_not_bookable(semester):
    bed = f.bed()
    bed.status = SpaceStatus.MAINTENANCE
    bed.save()
    s = student()
    with pytest.raises(HostelError):
        services.book_bed(s.user, s, bed, semester, SYSTEM)


def test_booking_needs_an_open_direct_window():
    semester = f.semester(is_current=True)
    s = student()
    with pytest.raises(HostelError, match="not open"):
        services.book_bed(s.user, s, f.bed(), semester, SYSTEM)


def test_student_cannot_book_for_someone_else(semester):
    me, other = student(), student()
    with pytest.raises(PermissionDenied):
        services.book_bed(me.user, other, f.bed(), semester, SYSTEM)


def test_cancel_frees_the_bed(semester):
    s = student()
    bed = f.bed()
    services.book_bed(s.user, s, bed, semester, SYSTEM)
    services.cancel(s.user, s, semester, SYSTEM)
    assert bed in selectors.free_beds(semester)


# --- Applications and offers ----------------------------------------------------------------------


@pytest.fixture
def application_semester():
    s = f.semester(is_current=True)
    f.booking_window(s, mode="APPLICATION", acceptance_hours=24)
    return s


def test_application_offer_accept_flow(application_semester, office):
    s = student()
    hostel = f.hostel()
    bed = f.bed(hostel)
    application = services.apply(s.user, s, application_semester, [hostel], "", "Ground floor please", SYSTEM)
    allocation = services.offer_bed(office, s, bed, application_semester, SYSTEM, application=application)
    application.refresh_from_db()
    assert allocation.status == AllocationStatus.PENDING_ACCEPTANCE and application.status == ApplicationStatus.ALLOCATED
    assert bed not in selectors.free_beds(application_semester)  # an offer holds the bed
    services.respond_to_offer(s.user, allocation, True, SYSTEM)
    allocation.refresh_from_db()
    assert allocation.status == AllocationStatus.ACTIVE


def test_declined_offer_frees_bed_and_reopens_application(application_semester, office):
    s = student()
    hostel = f.hostel()
    bed = f.bed(hostel)
    application = services.apply(s.user, s, application_semester, [hostel], "", "", SYSTEM)
    allocation = services.offer_bed(office, s, bed, application_semester, SYSTEM, application=application)
    services.respond_to_offer(s.user, allocation, False, SYSTEM)
    application.refresh_from_db()
    assert application.status == ApplicationStatus.APPROVED and bed in selectors.free_beds(application_semester)


def test_expired_offer_lapses_and_cannot_be_accepted(application_semester, office):
    s = student()
    bed = f.bed()
    allocation = services.offer_bed(office, s, bed, application_semester, SYSTEM)
    HostelAllocation.objects.filter(pk=allocation.pk).update(offer_expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(HostelError, match="no longer open"):
        services.respond_to_offer(s.user, HostelAllocation.objects.get(pk=allocation.pk), True, SYSTEM)
    assert HostelAllocation.objects.get(pk=allocation.pk).status == AllocationStatus.EXPIRED


def test_expired_offer_does_not_block_the_bed(application_semester, office):
    bed = f.bed()
    first = services.offer_bed(office, student(), bed, application_semester, SYSTEM)
    HostelAllocation.objects.filter(pk=first.pk).update(offer_expires_at=timezone.now() - timedelta(minutes=1))
    services.offer_bed(office, student(), bed, application_semester, SYSTEM)  # lazily expires the stale offer


def test_expire_command(application_semester, office):
    allocation = services.offer_bed(office, student(), f.bed(), application_semester, SYSTEM)
    HostelAllocation.objects.filter(pk=allocation.pk).update(offer_expires_at=timezone.now() - timedelta(hours=1))
    call_command("expire_hostel_offers")
    assert HostelAllocation.objects.get(pk=allocation.pk).status == AllocationStatus.EXPIRED


def test_one_open_application_per_semester_and_preference_rules(application_semester):
    s = student()
    h1, h2 = f.hostel(), f.hostel()
    with pytest.raises(HostelError, match="different"):
        services.apply(s.user, s, application_semester, [h1, h1], "", "", SYSTEM)
    services.apply(s.user, s, application_semester, [h1, h2], "", "", SYSTEM)
    with pytest.raises(HostelError, match="already have"):
        services.apply(s.user, s, application_semester, [h1], "", "", SYSTEM)


# --- Office actions -------------------------------------------------------------------------------


def test_transfer_and_vacate(semester, office):
    s = student()
    old_bed, new_bed = f.bed(), f.bed()
    allocation = services.book_bed(s.user, s, old_bed, semester, SYSTEM)
    new = services.transfer(office, allocation, new_bed, SYSTEM)
    allocation.refresh_from_db()
    assert allocation.status == AllocationStatus.TRANSFERRED and new.status == AllocationStatus.ACTIVE
    assert old_bed in selectors.free_beds(semester) and new_bed not in selectors.free_beds(semester)
    services.vacate(office, new, SYSTEM)
    assert new_bed in selectors.free_beds(semester)


def test_only_accommodation_office_allocates(semester):
    plain_admin = f.user(Role.ADMIN, groups=["Registrar"])
    with pytest.raises(PermissionDenied):
        services.offer_bed(plain_admin, student(), f.bed(), semester, SYSTEM)


def test_add_rooms_creates_beds(office):
    hostel = f.hostel()
    created = services.add_rooms(office, hostel, building="Block B", floor_level=2, first_room_number=201, rooms=3,
                                 room_type="TRIPLE", beds_per_room=3, fee_per_semester=1000, ctx=SYSTEM)
    assert created == 3 and hostel.buildings.get(name="Block B").floors.get(level=2).rooms.count() == 3


# --- Views ----------------------------------------------------------------------------------------


def test_catalog_counts_and_booking_through_the_ui(client, semester):
    hostel = f.hostel(gender_policy="MIXED")
    beds = [f.bed(hostel) for _ in range(3)]
    s = student()
    login(client, s.user)
    assert b"3 of 3 beds free" in client.get(reverse("hostels:catalog")).content
    response = client.post(reverse("hostels:book", args=[beds[0].pk]))
    assert response.status_code == 302 and response.url == reverse("hostels:my_hostel")
    assert b"2 of 3 beds free" in client.get(reverse("hostels:catalog")).content


def test_student_cannot_respond_to_another_students_offer(client, application_semester, office):
    victim = student()
    allocation = services.offer_bed(office, victim, f.bed(), application_semester, SYSTEM)
    attacker = student()
    login(client, attacker.user)
    response = client.post(reverse("hostels:respond", args=[allocation.pk]), {"decision": "decline"})
    assert response.status_code == 404
    assert HostelAllocation.objects.get(pk=allocation.pk).status == AllocationStatus.PENDING_ACCEPTANCE


def test_special_needs_are_private(client, application_semester, office):
    s = student()
    hostel = f.hostel()
    application = services.apply(s.user, s, application_semester, [hostel], "", "Wheelchair access needed", SYSTEM)
    login(client, f.user(Role.STAFF))
    assert client.get(reverse("hostels:application", args=[application.pk])).status_code == 403
    login(client, office)
    assert b"Wheelchair access needed" in client.get(reverse("hostels:application", args=[application.pk])).content


def test_management_requires_capability(client, semester):
    login(client, student().user)
    assert client.get(reverse("hostels:manage")).status_code == 403

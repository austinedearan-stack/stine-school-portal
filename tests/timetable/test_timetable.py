"""Timetable views, management and clash detection (Phase 6; spec §8)."""

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.academics.models import UnitRegistration
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.core.models import AuditLog
from apps.timetable import services
from apps.timetable.models import StudentGroup, TimetableEntry, Venue
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db
Conflict = services.TimetableConflict


@pytest.fixture
def semester():
    return f.semester(is_current=True)


@pytest.fixture
def timetabler():
    user = f.user(Role.STAFF, groups=["Timetabling"])
    enrol(user)
    return user


def venue(code="LH1", capacity=100):
    return Venue.objects.create(code=code, name=f"Hall {code}", capacity=capacity)


def slot(offering, room, day=1, start=9, end=11, group=None):
    return {"offering": offering, "venue": room, "day_of_week": day, "start_time": f.at(start),
            "end_time": f.at(end), "class_type": "LECTURE", "student_group": group}


def test_create_entry_denormalises_semester_and_lecturer_and_notifies(semester, timetabler):
    lecturer = f.staff()
    offering = f.offering(semester=semester, lecturer=lecturer)
    student = f.student()
    UnitRegistration.objects.create(student=student, offering=offering, unit=offering.unit, semester=semester)
    entry = services.create_entry(timetabler, slot(offering, venue()), SYSTEM)
    assert entry.semester == semester and entry.lecturer == lecturer
    assert student.user.notifications.filter(kind="TIMETABLE").exists()
    assert lecturer.user.notifications.filter(kind="TIMETABLE").exists()
    assert AuditLog.objects.filter(action="TIMETABLE.CREATED").exists()


def test_venue_clash_is_refused(semester, timetabler):
    room = venue()
    services.create_entry(timetabler, slot(f.offering(semester=semester), room), SYSTEM)
    with pytest.raises(Conflict, match="already booked"):
        services.create_entry(timetabler, slot(f.offering(semester=semester), room, start=10, end=12), SYSTEM)


def test_lecturer_clash_is_refused(semester, timetabler):
    lecturer = f.staff()
    services.create_entry(timetabler, slot(f.offering(semester=semester, lecturer=lecturer), venue("A")), SYSTEM)
    with pytest.raises(Conflict, match="already teaches"):
        services.create_entry(timetabler, slot(f.offering(semester=semester, lecturer=lecturer), venue("B")), SYSTEM)


def test_student_group_clash_is_refused(semester, timetabler):
    group = StudentGroup.objects.create(program=f.program(), year_of_study=1, label="G1")
    services.create_entry(timetabler, slot(f.offering(semester=semester), venue("A"), group=group), SYSTEM)
    with pytest.raises(Conflict, match="Group"):
        services.create_entry(timetabler, slot(f.offering(semester=semester), venue("B"), group=group), SYSTEM)


def test_back_to_back_and_other_days_are_fine(semester, timetabler):
    room = venue()
    services.create_entry(timetabler, slot(f.offering(semester=semester), room, start=9, end=11), SYSTEM)
    services.create_entry(timetabler, slot(f.offering(semester=semester), room, start=11, end=13), SYSTEM)
    services.create_entry(timetabler, slot(f.offering(semester=semester), room, day=2), SYSTEM)
    assert TimetableEntry.objects.count() == 3


def test_same_slot_in_another_semester_is_fine(semester, timetabler):
    room = venue()
    services.create_entry(timetabler, slot(f.offering(semester=semester), room), SYSTEM)
    services.create_entry(timetabler, slot(f.offering(semester=f.semester()), room), SYSTEM)


def test_end_before_start_is_refused(semester, timetabler):
    with pytest.raises(Conflict, match="after the start"):
        services.create_entry(timetabler, slot(f.offering(semester=semester), venue(), start=11, end=9), SYSTEM)


def test_venue_too_small_for_enrolment(semester, timetabler):
    offering = f.offering(semester=semester)
    for _ in range(3):
        s = f.student()
        UnitRegistration.objects.create(student=s, offering=offering, unit=offering.unit, semester=semester)
    with pytest.raises(Conflict, match="seats 2"):
        services.create_entry(timetabler, slot(offering, venue(capacity=2)), SYSTEM)


def test_moving_an_entry_does_not_clash_with_itself(semester, timetabler):
    offering = f.offering(semester=semester)
    entry = services.create_entry(timetabler, slot(offering, venue()), SYSTEM)
    services.update_entry(timetabler, entry, slot(offering, entry.venue, start=10, end=12), SYSTEM)
    entry.refresh_from_db()
    assert entry.start_time == f.at(10)


def test_changing_lecturer_rechecks_clashes(semester, timetabler):
    busy = f.staff()
    services.create_entry(timetabler, slot(f.offering(semester=semester, lecturer=busy), venue("A")), SYSTEM)
    other = f.offering(semester=semester)
    services.create_entry(timetabler, slot(other, venue("B")), SYSTEM)
    academic_office = f.user(Role.ADMIN, groups=["Academic Office"])
    with pytest.raises(Conflict):
        services.set_offering_lecturer(academic_office, other, busy, SYSTEM)
    free = f.staff()
    services.set_offering_lecturer(academic_office, other, free, SYSTEM)
    assert TimetableEntry.objects.get(offering=other).lecturer == free


def test_staff_without_capability_cannot_manage(semester):
    plain = f.user(Role.STAFF)
    with pytest.raises(PermissionDenied):
        services.create_entry(plain, slot(f.offering(semester=semester), venue()), SYSTEM)


# --- Views ----------------------------------------------------------------------------------------


def test_student_sees_only_registered_classes(client, semester, timetabler):
    student = f.student()
    mine = f.offering(semester=semester, unit=f.unit(title="Mine Unit"))
    other = f.offering(semester=semester, unit=f.unit(title="Not Mine Unit"))
    UnitRegistration.objects.create(student=student, offering=mine, unit=mine.unit, semester=semester)
    services.create_entry(timetabler, slot(mine, venue("A")), SYSTEM)
    services.create_entry(timetabler, slot(other, venue("B")), SYSTEM)
    login(client, student.user)
    page = client.get(reverse("timetable:my_timetable")).content.decode()
    assert "Mine Unit" in page and "Not Mine Unit" not in page
    master = client.get(reverse("timetable:master")).content.decode()
    assert "Mine Unit" in master and "Not Mine Unit" in master  # the master timetable is public


def test_management_pages_need_the_capability(client, semester, timetabler):
    login(client, f.student().user)
    assert client.get(reverse("timetable:manage")).status_code == 403
    login(client, f.staff().user)
    assert client.get(reverse("timetable:entry_create")).status_code == 403
    login(client, timetabler)
    assert client.get(reverse("timetable:manage")).status_code == 200


def test_clash_is_shown_on_the_form(client, semester, timetabler):
    room = venue()
    services.create_entry(timetabler, slot(f.offering(semester=semester), room), SYSTEM)
    offering = f.offering(semester=semester)
    login(client, timetabler)
    response = client.post(reverse("timetable:entry_create"), {
        "offering": offering.pk, "venue": room.pk, "day_of_week": 1, "start_time": "10:00", "end_time": "12:00",
        "class_type": "LECTURE"})
    assert response.status_code == 200 and b"already booked" in response.content
    assert TimetableEntry.objects.count() == 1


def test_delete_requires_post(client, semester, timetabler):
    entry = services.create_entry(timetabler, slot(f.offering(semester=semester), venue()), SYSTEM)
    login(client, timetabler)
    assert client.get(reverse("timetable:entry_delete", args=[entry.pk])).status_code == 405
    assert client.post(reverse("timetable:entry_delete", args=[entry.pk])).status_code == 302
    assert not TimetableEntry.objects.exists()

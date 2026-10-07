"""JSON API: own data only, session auth with CSRF, read-only (Phase 13; ARCHITECTURE.md D2)."""

import pytest
from django.test import Client

from apps.academics.models import UnitRegistration
from apps.core.capabilities import Role
from apps.notifications.services import notify
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


@pytest.fixture
def semester():
    return f.semester(is_current=True)


def test_anonymous_is_refused(client):
    for path in ("/api/v1/me/", "/api/v1/me/registrations/", "/api/v1/me/notifications/", "/api/v1/offerings/"):
        assert client.get(path).status_code == 403


def test_me_returns_own_record_without_secrets(client):
    student = f.student(phone="+254700000009")
    login(client, student.user)
    data = client.get("/api/v1/me/").json()
    assert data["username"] == student.user.username and data["student"]["student_number"] == student.student_number
    assert "password" not in data and "emergency_contact_phone" not in data["student"]


def test_registrations_are_own_and_parameters_cannot_switch_user(client, semester):
    me, other = f.student(), f.student()
    mine, theirs = f.offering(semester=semester), f.offering(semester=semester)
    UnitRegistration.objects.create(student=me, offering=mine, unit=mine.unit, semester=semester)
    UnitRegistration.objects.create(student=other, offering=theirs, unit=theirs.unit, semester=semester)
    login(client, me.user)
    data = client.get("/api/v1/me/registrations/", {"student": str(other.pk), "user": str(other.user_id)}).json()
    codes = [r["offering"]["unit_code"] for r in data["results"]]
    assert codes == [mine.unit.code]


def test_registrations_endpoint_is_student_only(client, semester):
    login(client, f.staff().user)
    assert client.get("/api/v1/me/registrations/").status_code == 403


def test_catalogue_search(client, semester):
    f.offering(semester=semester, unit=f.unit(title="Astrophysics"))
    f.offering(semester=semester, unit=f.unit(title="Botany"))
    login(client, f.student().user)
    titles = [o["title"] for o in client.get("/api/v1/offerings/", {"q": "astro"}).json()["results"]]
    assert titles == ["Astrophysics"]
    assert client.get("/api/v1/offerings/", {"q": "' OR 1=1 --"}).json()["results"] == []


def test_notifications_and_mark_read_require_csrf(semester):
    student = f.student()
    other = f.student()
    mine = notify(student.user, "SYSTEM", "Hello")
    theirs = notify(other.user, "SYSTEM", "Not yours")
    client = Client(enforce_csrf_checks=True)
    login(client, student.user)
    listed = [n["title"] for n in client.get("/api/v1/me/notifications/").json()["results"]]
    assert listed == ["Hello"]
    url = f"/api/v1/me/notifications/{mine.pk}/read/"
    assert client.post(url).status_code == 403  # no CSRF token
    client.get("/accounts/profile/")  # obtain the CSRF cookie
    token = client.cookies["csrftoken"].value
    assert client.post(url, HTTP_X_CSRFTOKEN=token).status_code == 204
    assert client.post(f"/api/v1/me/notifications/{theirs.pk}/read/", HTTP_X_CSRFTOKEN=token).status_code == 404


def test_api_respects_the_mfa_session_gate(client):
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    enrol(admin)
    login(client, admin, mfa_verified=False)
    response = client.get("/api/v1/me/")
    assert response.status_code in (302, 403)  # session ended by the policy middleware


def test_timetable_for_lecturer(client, semester):
    from apps.timetable.models import TimetableEntry, Venue

    lecturer = f.staff()
    offering = f.offering(semester=semester, lecturer=lecturer)
    TimetableEntry.objects.create(offering=offering, semester=semester, lecturer=lecturer,
                                  venue=Venue.objects.create(code="V9", name="V9"), day_of_week=3,
                                  start_time=f.at(8), end_time=f.at(10))
    login(client, lecturer.user)
    data = client.get("/api/v1/me/timetable/").json()
    assert data[0]["unit_code"] == offering.unit.code and data[0]["day"] == "Wednesday"


def test_api_is_read_only(client, semester):
    login(client, f.student().user)
    assert client.post("/api/v1/me/", {"role": "ADMIN"}).status_code == 405
    assert client.delete("/api/v1/me/registrations/").status_code == 405

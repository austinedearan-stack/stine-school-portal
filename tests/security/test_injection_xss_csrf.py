"""Spec §35 security categories: SQL injection, stored/reflected XSS and CSRF (ARCHITECTURE.md T8–T10)."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import Club
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.notifications.models import Announcement
from apps.notifications.services import save_announcement
from apps.student_requests import services as request_services
from apps.student_requests.models import RequestCategory
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db

SQLI = ["' OR '1'='1", "'; DROP TABLE accounts_user; --", "\" OR 1=1 --", "1) UNION SELECT password FROM accounts_user--",
        "%' OR '%'='"]
XSS = '<script>alert("xss")</script><img src=x onerror=alert(1)>'


def admin(*groups):
    user = f.user(Role.ADMIN, groups=groups)
    enrol(user)
    return user


# --- SQL injection --------------------------------------------------------------------------------


@pytest.mark.parametrize("payload", SQLI)
def test_search_fields_treat_injection_as_text(client, payload):
    semester = f.semester(is_current=True)
    f.offering(semester=semester)
    Club.objects.create(code="C1", name="Chess")
    login(client, f.student().user)
    for url in (reverse("academics:catalog"), reverse("clubs:directory")):
        response = client.get(url, {"q": payload})
        assert response.status_code == 200
        assert b"No " in response.content  # nothing matches the literal text
    login(client, admin("IT Support", "Auditor"))
    for url, param in ((reverse("administration:users"), "q"), (reverse("administration:audit_log"), "action")):
        assert client.get(url, {param: payload}).status_code == 200
    from apps.accounts.models import User

    assert User.objects.exists()  # the table is still there


def test_injection_in_login_identifier_is_just_a_failed_login(client):
    user = f.user()
    response = client.post(reverse("accounts:login"), {"identifier": f"{user.username}' --", "password": "x"})
    assert response.status_code == 200 and "_auth_user_id" not in client.session


# --- XSS ------------------------------------------------------------------------------------------


def _assert_escaped(response):
    content = response.content.decode()
    assert "<script>alert" not in content and "<img src=x onerror" not in content  # never raw markup
    assert "&lt;script&gt;" in content  # ...but shown as text


def test_stored_xss_in_request_is_escaped_for_staff(client):
    student = f.student()
    req = request_services.submit(student.user, student, RequestCategory.objects.get(code="GENERAL"), XSS, XSS, SYSTEM)
    request_services.add_message(student.user, req, XSS, SYSTEM)
    reviewer = f.staff(department=student.program.department, groups=["Department Reviewers"])
    login(client, reviewer.user)
    _assert_escaped(client.get(reverse("requests:detail", args=[req.pk])))
    _assert_escaped(client.get(reverse("requests:queue")))


def test_stored_xss_in_announcement_and_club_is_escaped(client):
    author = admin("Communications")
    announcement = save_announcement(author, Announcement(title=XSS, body=XSS, publish_at=timezone.now()), SYSTEM,
                                     publish=True)
    Club.objects.create(code="XSS", name="X Club", description=XSS)
    viewer = f.student()
    login(client, viewer.user)
    _assert_escaped(client.get(reverse("notifications:announcement", args=[announcement.pk])))
    _assert_escaped(client.get(reverse("clubs:directory")))


def test_stored_xss_in_profile_is_escaped_for_the_registrar(client):
    student = f.student(emergency_contact_name=XSS)
    login(client, admin("Registrar"))
    _assert_escaped(client.get(reverse("accounts:student_detail", args=[student.pk])))


def test_reflected_search_input_is_escaped(client):
    f.semester(is_current=True)
    login(client, f.student().user)
    _assert_escaped(client.get(reverse("academics:catalog"), {"q": "<script>alert(1)</script>"}))


# --- CSRF -----------------------------------------------------------------------------------------


def test_state_changing_endpoints_reject_requests_without_csrf_token():
    semester = f.semester(is_current=True)
    offering = f.offering(semester=semester)
    student = f.student()
    client = Client(enforce_csrf_checks=True)
    login(client, student.user)
    for url, data in (
        (reverse("academics:register", args=[offering.pk]), {}),
        (reverse("accounts:edit_profile"), {"phone": "1"}),
        (reverse("requests:create"), {"category": RequestCategory.objects.get(code="GENERAL").pk, "subject": "x",
                                      "description": "y"}),
        (reverse("accounts:logout"), {}),
        (reverse("notifications:read_all"), {}),
    ):
        response = client.post(url, data)
        assert response.status_code == 403, url
        assert b"could not be verified" in response.content
    student.refresh_from_db()
    assert student.phone == ""


def test_valid_csrf_token_is_accepted():
    student = f.student()
    client = Client(enforce_csrf_checks=True)
    login(client, student.user)
    client.get(reverse("accounts:edit_profile"))
    token = client.cookies["csrftoken"].value
    response = client.post(reverse("accounts:edit_profile"), {"phone": "+254700000077", "csrfmiddlewaretoken": token})
    assert response.status_code == 302

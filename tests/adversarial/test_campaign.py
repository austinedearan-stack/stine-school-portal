"""Phase 14 adversarial campaign: attacks not already pinned by the feature and security suites.

Each test is written from the attacker's side: the attack, then what the portal must do. Results are
recorded in SECURITY_TEST_REPORT.md (P14-xx).
"""

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts import admin_services, mfa
from apps.accounts.models import User
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.hostels.models import Room
from apps.student_requests.models import RequestCategory, StudentRequest
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


# P14-01 Homoglyph twin accounts ---------------------------------------------------------------------


def test_fullwidth_lookalike_username_cannot_create_a_twin_account():
    User.objects.create_user("S123", "s123@example.test", "x-Very-long-passphrase-1")
    with pytest.raises(ValidationError):
        User.objects.create_user("Ｓ123", "twin@example.test", "x-Very-long-passphrase-1")  # NFKC -> "S123"
    with pytest.raises(ValidationError):
        User.objects.create_user("S123е", "cyr@example.test", "x-Very-long-passphrase-1")  # Cyrillic "е"


def test_lookalike_identifier_signs_in_to_the_canonical_account_and_shares_its_throttle(client):
    user = f.user(username="S777")
    response = client.post(reverse("accounts:login"), {"identifier": "Ｓ７７７", "password": f.PASSWORD})
    assert response.status_code == 302 and client.session["_auth_user_id"] == str(user.pk)
    from apps.accounts.throttle import subject_for

    assert subject_for("Ｓ７７７") == subject_for("s777")


# P14-02 Session replay after logout ----------------------------------------------------------------


def test_stolen_session_cookie_is_useless_after_logout():
    victim = f.user()
    browser = Client()
    browser.post(reverse("accounts:login"), {"identifier": victim.username, "password": f.PASSWORD})
    stolen = browser.cookies["sessionid"].value
    browser.post(reverse("accounts:logout"))
    attacker = Client()
    attacker.cookies["sessionid"] = stolen
    assert attacker.get(reverse("core:dashboard")).status_code == 302


# P14-03 Forged device cookie ------------------------------------------------------------------------


def test_forged_device_cookie_does_not_bypass_the_lockout(settings):
    victim = f.user()
    attacker = Client(REMOTE_ADDR="198.51.100.7")
    for _ in range(5):
        attacker.post(reverse("accounts:login"), {"identifier": victim.username, "password": "guess-guess-guess"})
    attacker.cookies[settings.DEVICE_COOKIE_NAME] = "eyJ1IjoiZm9yZ2VkIn0:forged:signature"
    response = attacker.post(reverse("accounts:login"), {"identifier": victim.username, "password": f.PASSWORD})
    assert response.status_code == 429


# P14-04 Host header injection -----------------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=["portal.example.test"])
def test_unknown_host_header_is_refused():
    response = Client().get(reverse("accounts:login"), HTTP_HOST="evil.example.com")
    assert response.status_code == 400


# P14-05 Input abuse ---------------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["-1", "0", "abc", "99999999", "1e9"])
def test_pagination_abuse_is_harmless(client, page):
    f.semester(is_current=True)
    login(client, f.student().user)
    assert client.get(reverse("academics:catalog"), {"page": page}).status_code == 200


def test_null_bytes_and_oversized_fields_are_rejected_cleanly(client):
    student = f.student()
    login(client, student.user)
    category = RequestCategory.objects.get(code="GENERAL").pk
    for subject in ("abc\x00def", "x" * 500):
        response = client.post(reverse("requests:create"), {"category": category, "subject": subject,
                                                            "description": "d"})
        assert response.status_code == 200  # form error, not a 500
    assert not StudentRequest.objects.exists()
    assert client.get(reverse("academics:catalog"), {"q": "q" * 10_000}).status_code == 200


def test_megabyte_password_is_refused_before_hashing(client):
    user = f.user()
    response = client.post(reverse("accounts:login"), {"identifier": user.username, "password": "a" * 1_000_000})
    assert response.status_code in (200, 413) and "_auth_user_id" not in client.session


def test_negative_and_out_of_range_numbers_are_refused(client):
    office = f.user(Role.ADMIN, groups=["Accommodation Office"])
    enrol(office)
    hostel = f.hostel()
    login(client, office)
    client.post(reverse("hostels:hostel_edit", args=[hostel.pk]), {
        "form": "rooms", "building": "A", "floor_level": 1, "first_room_number": 1, "rooms": -5,
        "room_type": "DOUBLE", "beds_per_room": 99, "fee_per_semester": "-1"})
    assert not Room.objects.exists()


# P14-06 HTTP verb tampering --------------------------------------------------------------------------


def test_state_changing_endpoints_refuse_get_and_other_verbs(client):
    semester = f.semester(is_current=True)
    offering = f.offering(semester=semester)
    login(client, f.student().user)
    url = reverse("academics:register", args=[offering.pk])
    for method in ("get", "put", "patch", "delete"):
        assert getattr(client, method)(url).status_code == 405


# P14-07 Hidden-field / branch tampering ------------------------------------------------------------


def test_student_cannot_reach_office_branches_by_tampering_hidden_fields(client):
    semester = f.semester(is_current=True)
    f.booking_window(semester, mode="APPLICATION")
    student = f.student()
    from apps.hostels import services as hostel_services

    application = hostel_services.apply(student.user, student, semester, [f.hostel()], "", "", SYSTEM)
    login(client, student.user)
    response = client.post(reverse("hostels:application", args=[application.pk]), {"form": "offer", "bed": f.bed().pk})
    assert response.status_code == 403


# P14-08 Sensitive paths and traversal ----------------------------------------------------------------


@pytest.mark.parametrize("path", ["/.env", "/.git/config", "/django-admin/", "/admin/", "/static/../manage.py",
                                  "/static/%2e%2e/manage.py", "/media/", "/private-media-not-served/x",
                                  "/accounts/../.env"])
def test_sensitive_paths_are_not_served(client, path):
    response = client.get(path)
    assert response.status_code in (302, 404)
    assert b"SECRET_KEY" not in response.content and b"[core]" not in response.content


# P14-09 Self-escalation ----------------------------------------------------------------------------


def test_superadmin_cannot_change_own_groups_and_admin_cannot_grant_itself_anything():
    superadmin = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    with pytest.raises(PermissionDenied):
        admin_services.set_groups(superadmin, superadmin, [], SYSTEM)
    admin = f.user(Role.ADMIN, groups=["IT Support"])
    with pytest.raises(PermissionDenied):
        admin_services.set_groups(admin, admin, ["Registrar"], SYSTEM)


def test_open_redirect_through_reauthentication_next_is_refused(client):
    user = f.user()
    login(client, user)
    response = client.post(reverse("accounts:reauth"), {"password": f.PASSWORD, "next": "https://evil.example.com/"})
    assert response.status_code == 302 and response.url == reverse("accounts:security")


# P14-10 Enrollment-code brute force ----------------------------------------------------------------


def test_enrollment_code_guessing_is_cut_off():
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    mfa.issue_enrollment_code(admin, issued_by=None)
    attacker = Client()
    attacker.post(reverse("accounts:login"), {"identifier": admin.username, "password": f.PASSWORD})
    statuses = [attacker.post(reverse("accounts:mfa_enroll"), {"enrollment_code": f"AAAA-BBBB-CCCC-{i:04d}"}).status_code
                for i in range(5)]
    assert statuses[-1] == 302  # fifth wrong code: pre-auth discarded, back to sign-in
    assert attacker.get(reverse("accounts:mfa_enroll")).url == reverse("accounts:login")


# P14-11 Malicious filename in UI -------------------------------------------------------------------


def test_script_in_attachment_filename_is_neutralised(client, settings, tmp_path):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.student_requests import services as request_services

    settings.MEDIA_ROOT = tmp_path
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="PNG")
    student = f.student()
    upload = SimpleUploadedFile('"><script>alert(1)</script>.png', buffer.getvalue(), content_type="image/png")
    req = request_services.submit(student.user, student, RequestCategory.objects.get(code="GENERAL"), "s", "d", SYSTEM,
                                  uploads=[upload])
    stored = req.attachments.get().file
    assert "<" not in stored.original_name and ">" not in stored.original_name
    login(client, student.user)
    assert b"<script>alert(1)" not in client.get(reverse("requests:detail", args=[req.pk])).content

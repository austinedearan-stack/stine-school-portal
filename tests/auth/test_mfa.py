"""MFA: verification, replay, recovery codes, enrollment codes and pre-auth state (ARCHITECTURE.md §4.2; A-1, A-7, A-8)."""

import time

import pyotp
import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts import mfa, sessions
from apps.accounts.models import MFADevice, MFAEnrollmentCode
from apps.core.capabilities import Role
from apps.core.crypto import decrypt
from apps.core.models import SecurityEvent, SecurityEventType
from tests import factories as f
from tests.helpers import enrol, login, totp_code

pytestmark = pytest.mark.django_db
LOGIN = "/accounts/login/"


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def admin_with_device():
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    return admin, enrol(admin)


def password_step(client, user):
    response = client.post(LOGIN, {"identifier": user.username, "password": f.PASSWORD})
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session  # password alone never logs an MFA user in
    return response


def test_admin_must_complete_mfa(client, admin_with_device):
    admin, secret = admin_with_device
    assert password_step(client, admin).url == reverse("accounts:mfa_verify")
    assert client.get(reverse("core:dashboard")).status_code == 302  # still anonymous
    response = client.post(reverse("accounts:mfa_verify"), {"code": totp_code(secret)})
    assert response.status_code == 302 and client.session["_auth_user_id"] == str(admin.pk)
    assert client.session[sessions.MFA_VERIFIED_KEY]
    assert client.get(reverse("core:dashboard")).status_code == 200


def test_totp_code_cannot_be_replayed(client, admin_with_device):
    admin, secret = admin_with_device
    code = totp_code(secret)
    password_step(client, admin)
    client.post(reverse("accounts:mfa_verify"), {"code": code})
    client.post(reverse("accounts:logout"))
    password_step(client, admin)
    response = client.post(reverse("accounts:mfa_verify"), {"code": code})
    assert response.status_code == 200 and "_auth_user_id" not in client.session


def test_older_step_code_is_refused_after_a_newer_one(admin_with_device):
    admin, secret = admin_with_device
    device = MFADevice.objects.get(user=admin)
    assert mfa.verify_totp(device, totp_code(secret, offset_steps=1))
    device.refresh_from_db()
    assert not mfa.verify_totp(device, totp_code(secret, offset_steps=0))  # audit A-7


def test_recovery_code_works_once(client, admin_with_device):
    admin, _ = admin_with_device
    code = mfa.issue_recovery_codes(admin)[0]
    password_step(client, admin)
    assert client.post(reverse("accounts:mfa_verify"), {"code": code.lower()}).status_code == 302
    client.post(reverse("accounts:logout"))
    password_step(client, admin)
    assert client.post(reverse("accounts:mfa_verify"), {"code": code}).status_code == 200
    assert mfa.remaining_recovery_codes(admin) == 9


def test_five_wrong_codes_discard_preauth(client, admin_with_device):
    admin, secret = admin_with_device
    password_step(client, admin)
    for _ in range(4):
        assert client.post(reverse("accounts:mfa_verify"), {"code": "000000"}).status_code == 200
    response = client.post(reverse("accounts:mfa_verify"), {"code": "000000"})
    assert response.status_code == 302 and response.url == reverse("accounts:login")
    assert sessions.PREAUTH_KEY not in client.session
    assert client.post(reverse("accounts:mfa_verify"), {"code": totp_code(secret)}).url == reverse("accounts:login")


def test_preauth_expires(client, admin_with_device, monkeypatch):
    admin, secret = admin_with_device
    password_step(client, admin)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + sessions.PREAUTH_TTL + 5)
    response = client.post(reverse("accounts:mfa_verify"), {"code": totp_code(secret)})
    assert response.status_code == 302 and response.url == reverse("accounts:login")


def test_preauth_dies_when_password_changes(client, admin_with_device):
    admin, secret = admin_with_device
    password_step(client, admin)
    admin.set_password("A-different-Passphrase-77")
    admin.save()
    response = client.post(reverse("accounts:mfa_verify"), {"code": totp_code(secret)})
    assert response.url == reverse("accounts:login") and "_auth_user_id" not in client.session


# --- Enrollment -------------------------------------------------------------------------------------


def test_first_enrollment_requires_an_out_of_band_code(client):
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    assert password_step(client, admin).url == reverse("accounts:mfa_enroll")
    response = client.post(reverse("accounts:mfa_enroll"), {"enrollment_code": "AAAA-BBBB-CCCC-DDDD"})
    assert response.status_code == 200 and b"not valid" in response.content
    # A QR code / secret is never shown before a valid enrollment code is presented.
    assert b"data:image/png" not in client.get(reverse("accounts:mfa_enroll")).content


def test_full_enrollment_flow(client):
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    code = mfa.issue_enrollment_code(admin, issued_by=None)
    password_step(client, admin)
    assert client.post(reverse("accounts:mfa_enroll"), {"enrollment_code": code}).status_code == 302
    page = client.get(reverse("accounts:mfa_enroll"))
    secret = page.context["secret"]
    response = client.post(reverse("accounts:mfa_enroll"), {"code": pyotp.TOTP(secret).now()})
    assert response.status_code == 200 and len(response.context["codes"]) == mfa.RECOVERY_CODE_COUNT
    assert client.session["_auth_user_id"] == str(admin.pk)
    device = MFADevice.objects.get(user=admin)
    assert device.confirmed_at and decrypt(device.secret_encrypted) == secret
    assert secret not in device.secret_encrypted  # encrypted at rest
    assert MFAEnrollmentCode.objects.get(user=admin).used_at is not None
    assert SecurityEvent.objects.filter(event_type=SecurityEventType.MFA_ENROLLED, user=admin).exists()


def test_wrong_totp_at_enrollment_keeps_the_enrollment_code_usable(client):
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    code = mfa.issue_enrollment_code(admin, issued_by=None)
    password_step(client, admin)
    client.post(reverse("accounts:mfa_enroll"), {"enrollment_code": code})
    client.post(reverse("accounts:mfa_enroll"), {"code": "123456"})
    assert MFAEnrollmentCode.objects.get(user=admin).used_at is None
    assert not MFADevice.objects.filter(user=admin).exists()


def test_expired_enrollment_code_is_refused(client):
    from django.utils import timezone

    admin = f.user(Role.ADMIN, groups=["Auditor"])
    code = mfa.issue_enrollment_code(admin, issued_by=None)
    MFAEnrollmentCode.objects.filter(user=admin).update(expires_at=timezone.now())
    password_step(client, admin)
    response = client.post(reverse("accounts:mfa_enroll"), {"enrollment_code": code})
    assert response.status_code == 200 and b"not valid" in response.content


def test_staff_with_a_capability_must_use_mfa(client):
    staff = f.user(Role.STAFF, groups=["Timetabling"])
    assert mfa.is_mfa_required(staff)
    assert password_step(client, staff).url == reverse("accounts:mfa_enroll")
    plain_staff = f.user(Role.STAFF)
    assert not mfa.is_mfa_required(plain_staff)


def test_logged_in_replacement_requires_reauthentication(client, admin_with_device):
    admin, secret = admin_with_device
    login(client, admin)
    response = client.get(reverse("accounts:mfa_setup"))
    assert response.status_code == 302 and response.url.startswith(reverse("accounts:reauth"))
    # Password alone is not enough to re-authenticate an enrolled user.
    response = client.post(reverse("accounts:reauth"), {"password": f.PASSWORD, "code": "000000",
                                                        "next": reverse("accounts:mfa_setup")})
    assert response.status_code == 200
    response = client.post(reverse("accounts:reauth"), {"password": f.PASSWORD, "code": totp_code(secret),
                                                        "next": reverse("accounts:mfa_setup")})
    assert response.status_code == 302 and response.url == reverse("accounts:mfa_setup")
    page = client.get(reverse("accounts:mfa_setup"))
    new_secret = page.context["secret"]
    # Until the new device is confirmed the old one keeps working.
    assert decrypt(MFADevice.objects.get(user=admin).secret_encrypted) == secret
    client.post(reverse("accounts:mfa_setup"), {"code": pyotp.TOTP(new_secret).now()})
    assert decrypt(MFADevice.objects.get(user=admin).secret_encrypted) == new_secret


def test_required_mfa_cannot_be_turned_off(client, admin_with_device):
    admin, secret = admin_with_device
    login(client, admin)
    client.post(reverse("accounts:reauth"), {"password": f.PASSWORD, "code": totp_code(secret), "next": "/"})
    client.post(reverse("accounts:mfa_disable"))
    assert MFADevice.objects.filter(user=admin).exists()


def test_student_can_opt_in_and_is_then_challenged(client):
    student = f.user(Role.STUDENT)
    login(client, student)
    client.post(reverse("accounts:reauth"), {"password": f.PASSWORD, "next": reverse("accounts:mfa_setup")})
    secret = client.get(reverse("accounts:mfa_setup")).context["secret"]
    client.post(reverse("accounts:mfa_setup"), {"code": pyotp.TOTP(secret).now()})
    assert client.get(reverse("core:dashboard")).status_code == 200  # this session proved the factor
    client.post(reverse("accounts:logout"))
    assert password_step(client, student).url == reverse("accounts:mfa_verify")

"""Regression test for audit finding A-1 (critical): MFA bypass via the enrollment page.

Attack: with only the victim's password, reach the pre-auth state, then open the enrollment page
instead of the verification page and enroll an attacker-controlled authenticator.
"""

import pyotp
import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.models import MFADevice, User
from apps.core.crypto import decrypt, encrypt
from apps.core.models import SecurityEvent
from tests.helpers import enrol, login

PASSWORD = "Adm1n!Test-Passphrase"  # test fixture only  # secret-scan: allow


@pytest.fixture
def mfa_admin(db):
    cache.clear()
    user = User.objects.create_user(username="admin001", email="admin@example.test", password=PASSWORD, role="ADMIN")
    secret = enrol(user)
    return user, secret


def _password_step(client, user):
    response = client.post(reverse("accounts:login"), {"identifier": user.username, "password": PASSWORD})
    assert response.status_code == 302 and response.url == reverse("accounts:mfa_verify")
    assert "_auth_user_id" not in client.session  # not logged in after password alone


def test_preauth_session_cannot_open_enrollment_for_enrolled_account(client, mfa_admin):
    user, _ = mfa_admin
    _password_step(client, user)
    response = client.get(reverse("accounts:mfa_enroll"))
    assert response.status_code == 302 and response.url == reverse("accounts:mfa_verify")


def test_preauth_session_cannot_complete_enrollment_by_posting(client, mfa_admin):
    user, original_secret = mfa_admin
    _password_step(client, user)
    attacker_secret = pyotp.random_base32()
    session = client.session
    session["pending_mfa_secret"] = encrypt(attacker_secret)  # even if an attacker could plant one
    session["preauth"] = {**session["preauth"], "enroll_digest": "x" * 64}
    session.save()
    response = client.post(reverse("accounts:mfa_enroll"), {"code": pyotp.TOTP(attacker_secret).now()})
    assert response.status_code == 302
    assert decrypt(MFADevice.objects.get(user=user).secret_encrypted) == original_secret
    assert "_auth_user_id" not in client.session


def test_preauth_session_cannot_use_the_logged_in_setup_page(client, mfa_admin):
    user, _ = mfa_admin
    _password_step(client, user)
    response = client.get(reverse("accounts:mfa_setup"))
    assert response.status_code == 302 and response.url.startswith(reverse("accounts:login"))


def test_blocked_attempt_is_recorded_as_security_event(client, mfa_admin):
    user, _ = mfa_admin
    _password_step(client, user)
    client.get(reverse("accounts:mfa_enroll"))
    assert SecurityEvent.objects.filter(
        event_type="MFA_FAILURE", user=user, details__reason="enrollment_blocked_device_exists"
    ).exists()


def test_logged_in_user_with_device_cannot_silently_replace_it(client, mfa_admin):
    user, _ = mfa_admin
    login(client, user)
    response = client.get(reverse("accounts:mfa_setup"))
    assert response.status_code == 302 and response.url.startswith(reverse("accounts:reauth"))

"""Regression test for audit finding A-1 (critical): MFA bypass via the enrollment page.

Attack: with only the victim's password, reach the "pre-MFA" state, then open
/accounts/mfa/setup/ instead of /accounts/mfa/verify/ and enroll an attacker-controlled
authenticator, completing login without the victim's second factor.
"""

import pyotp
import pytest
from django.urls import reverse

from apps.accounts.models import User

PASSWORD = "Adm1n!Test-Passphrase"  # test fixture only  # secret-scan: allow


@pytest.fixture
def mfa_admin(db):
    user = User.objects.create_user(
        username="admin001", email="admin@example.test", password=PASSWORD, role="ADMIN"
    )
    user.mfa_secret = pyotp.random_base32()
    user.is_mfa_enabled = True
    user.save()
    return user


def _password_step(client, user):
    response = client.post(reverse("accounts:login"), {"identifier": user.username, "password": PASSWORD})
    assert response.status_code == 302 and response.url == reverse("accounts:mfa_verify")
    assert "_auth_user_id" not in client.session  # not logged in after password alone


def test_preauth_session_cannot_open_enrollment_for_enrolled_account(client, mfa_admin):
    _password_step(client, mfa_admin)
    response = client.get(reverse("accounts:mfa_setup"))
    assert response.status_code == 302 and response.url == reverse("accounts:mfa_verify")
    assert "pending_mfa_secret" not in client.session


def test_preauth_session_cannot_complete_enrollment_by_posting(client, mfa_admin):
    _password_step(client, mfa_admin)
    attacker_secret = pyotp.random_base32()
    session = client.session
    session["pending_mfa_secret"] = attacker_secret  # even if attacker could plant a pending secret
    session.save()
    response = client.post(reverse("accounts:mfa_setup"), {"code": pyotp.TOTP(attacker_secret).now()})
    assert response.status_code == 302
    mfa_admin.refresh_from_db()
    assert mfa_admin.mfa_secret != attacker_secret
    assert "_auth_user_id" not in client.session


def test_blocked_attempt_is_recorded_as_security_event(client, mfa_admin):
    from apps.core.models import SecurityEventLog

    _password_step(client, mfa_admin)
    client.get(reverse("accounts:mfa_setup"))
    assert SecurityEventLog.objects.filter(
        event_type="MFA_FAILURE", user=mfa_admin, details__reason="enrollment_blocked_device_exists"
    ).exists()


def test_logged_in_user_with_device_cannot_silently_replace_it(client, mfa_admin):
    client.force_login(mfa_admin)
    response = client.get(reverse("accounts:mfa_setup"))
    assert response.status_code == 403

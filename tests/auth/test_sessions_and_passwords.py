"""Session policy (timeouts, MFA gate, forced change) and password change/reset (§4.3, §4.4, D15; A-4, A-10, A-11)."""

import re
import time

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts import sessions
from apps.accounts.models import PasswordResetCode, TrustedDevice
from apps.core.capabilities import Role
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db
NEW_PASSWORD = "Brand-New-Passphrase-42"  # test fixture only  # secret-scan: allow


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()
    yield
    cache.clear()


def _age_session(client, *, idle: int = 0, since_login: int = 0):
    session = client.session
    now = int(time.time())
    session[sessions.LAST_ACTIVITY_KEY] = now - idle
    session[sessions.AUTH_TIME_KEY] = now - since_login
    session.save()


# --- Session policy ---------------------------------------------------------------------------------


def test_idle_timeout_logs_student_out(client, settings):
    login(client, f.user(Role.STUDENT))
    _age_session(client, idle=settings.SESSION_IDLE_TIMEOUT + 5, since_login=settings.SESSION_IDLE_TIMEOUT + 5)
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 302 and "_auth_user_id" not in client.session


def test_admin_idle_timeout_is_shorter(client, settings):
    admin = f.user(Role.ADMIN)
    enrol(admin)
    login(client, admin)
    _age_session(client, idle=settings.SESSION_IDLE_TIMEOUT_ADMIN + 5, since_login=settings.SESSION_IDLE_TIMEOUT_ADMIN + 5)
    assert client.get(reverse("core:dashboard")).status_code == 302


def test_absolute_lifetime_is_enforced_even_when_active(client, settings):
    login(client, f.user(Role.STUDENT))
    _age_session(client, idle=0, since_login=settings.SESSION_ABSOLUTE_TIMEOUT + 5)
    assert client.get(reverse("core:dashboard")).status_code == 302


def test_mfa_required_session_without_mfa_stamp_is_refused(client):
    admin = f.user(Role.ADMIN)
    enrol(admin)
    login(client, admin, mfa_verified=False)  # e.g. a view forgot a check, or a forged session
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 302 and "_auth_user_id" not in client.session


def test_forced_password_change_redirects_everything_else(client):
    user = f.user(Role.STUDENT, must_change_password=True)
    login(client, user)
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 302 and response.url == reverse("accounts:password_change")
    client.post(reverse("accounts:password_change"), {"current_password": f.PASSWORD, "new_password": NEW_PASSWORD,
                                                      "confirm_password": NEW_PASSWORD})
    assert client.get(reverse("core:dashboard")).status_code == 200


def test_authenticated_pages_are_not_cacheable(client):
    login(client, f.user())
    assert "no-store" in client.get(reverse("core:dashboard"))["Cache-Control"]


# --- Password change --------------------------------------------------------------------------------


def test_password_change_requires_current_password(client):
    user = f.user()
    login(client, user)
    response = client.post(reverse("accounts:password_change"), {
        "current_password": "wrong", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password(f.PASSWORD)


def test_password_change_signs_out_other_sessions_and_notifies(client):
    user = f.user()
    other = Client()
    other.post(reverse("accounts:login"), {"identifier": user.username, "password": f.PASSWORD})
    assert other.get(reverse("core:dashboard")).status_code == 200
    client.post(reverse("accounts:login"), {"identifier": user.username, "password": f.PASSWORD})
    response = client.post(reverse("accounts:password_change"), {
        "current_password": f.PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert response.status_code == 302
    assert client.get(reverse("core:dashboard")).status_code == 200  # this session survives
    assert other.get(reverse("core:dashboard")).status_code == 302  # the other one does not
    assert not TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).exists()
    assert any("password was changed" in m.subject for m in mail.outbox)


def test_weak_and_overlong_passwords_are_refused(client):
    user = f.user()
    login(client, user)
    for bad in ("short", "password1234", "1234567890123", "x" * 129):
        response = client.post(reverse("accounts:password_change"), {
            "current_password": f.PASSWORD, "new_password": bad, "confirm_password": bad})
        assert response.status_code == 200, bad


# --- Password reset ---------------------------------------------------------------------------------


def _code_from_mail() -> str:
    match = re.search(r"code is: ([A-Z0-9]{5}-[A-Z0-9]{5})", mail.outbox[-1].body)
    assert match
    return match.group(1)


def test_reset_request_response_is_identical_for_unknown_accounts(client):
    user = f.user()
    a = client.post(reverse("accounts:password_reset_request"), {"identifier": user.email}, follow=True)
    b = client.post(reverse("accounts:password_reset_request"), {"identifier": "ghost@example.test"}, follow=True)
    assert a.status_code == b.status_code == 200
    assert a.redirect_chain == b.redirect_chain
    strip = lambda r: re.sub(r'value="[^"]+"', "", r.content.decode())  # noqa: E731 - csrf tokens differ
    assert strip(a) == strip(b)
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [user.email]


def test_reset_code_never_appears_in_a_url_and_is_stored_hashed(client):
    user = f.user()
    client.post(reverse("accounts:password_reset_request"), {"identifier": user.username})
    code = _code_from_mail()
    assert "http" not in mail.outbox[-1].body
    stored = PasswordResetCode.objects.get(user=user).code_hash
    assert code.replace("-", "") not in stored and len(stored) == 64


def test_full_reset_flow_ends_sessions_and_is_single_use(client):
    user = f.user()
    victim_session = Client()
    login(victim_session, user)
    client.post(reverse("accounts:password_reset_request"), {"identifier": user.username})
    code = _code_from_mail()
    data = {"identifier": user.username, "code": code, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}
    response = client.post(reverse("accounts:password_reset_confirm"), data)
    assert response.status_code == 302 and response.url == reverse("accounts:login")
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert victim_session.get(reverse("core:dashboard")).status_code == 302
    again = client.post(reverse("accounts:password_reset_confirm"), {**data, "new_password": "Another-Pass-Phrase-9",
                                                                      "confirm_password": "Another-Pass-Phrase-9"})
    assert again.status_code == 200 and b"not valid" in again.content


def test_reset_code_dies_after_five_wrong_attempts(client):
    user = f.user()
    client.post(reverse("accounts:password_reset_request"), {"identifier": user.username})
    code = _code_from_mail()
    for _ in range(5):
        client.post(reverse("accounts:password_reset_confirm"), {
            "identifier": user.username, "code": "WRONG-CODE1", "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD})
    response = client.post(reverse("accounts:password_reset_confirm"), {
        "identifier": user.username, "code": code, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password(f.PASSWORD)


def test_password_change_invalidates_outstanding_reset_codes(client):
    user = f.user()
    client.post(reverse("accounts:password_reset_request"), {"identifier": user.username})
    code = _code_from_mail()
    login(client, user)
    client.post(reverse("accounts:password_change"), {"current_password": f.PASSWORD, "new_password": NEW_PASSWORD,
                                                      "confirm_password": NEW_PASSWORD})
    client.post(reverse("accounts:logout"))
    response = client.post(reverse("accounts:password_reset_confirm"), {
        "identifier": user.username, "code": code, "new_password": "Third-Passphrase-Here-1",
        "confirm_password": "Third-Passphrase-Here-1"})
    assert response.status_code == 200


def test_reset_does_not_bypass_mfa(client):
    admin = f.user(Role.ADMIN)
    enrol(admin)
    client.post(reverse("accounts:password_reset_request"), {"identifier": admin.username})
    code = _code_from_mail()
    client.post(reverse("accounts:password_reset_confirm"), {
        "identifier": admin.username, "code": code, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    response = client.post(reverse("accounts:login"), {"identifier": admin.username, "password": NEW_PASSWORD})
    assert response.url == reverse("accounts:mfa_verify") and "_auth_user_id" not in client.session

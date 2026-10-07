"""Login, logout, enumeration resistance, redirects and session fixation (ARCHITECTURE.md §4.1, T3, T14, T17)."""

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.models import TrustedDevice, UserSession
from apps.core.capabilities import Role
from apps.core.models import SecurityEvent, SecurityEventType
from tests import factories as f

pytestmark = pytest.mark.django_db
LOGIN = "/accounts/login/"


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()
    yield
    cache.clear()


def post_login(client, identifier, password=f.PASSWORD, **extra):
    return client.post(LOGIN, {"identifier": identifier, "password": password, **extra})


def test_student_logs_in_with_username_or_email(client):
    user = f.user(Role.STUDENT)
    response = post_login(client, user.username.upper())  # case-insensitive
    assert response.status_code == 302 and response.url == reverse("core:dashboard")
    assert client.session["_auth_user_id"] == str(user.pk)
    client.post(reverse("accounts:logout"))
    response = post_login(client, user.email)
    assert response.status_code == 302


def test_failure_responses_are_identical_for_every_cause(client):
    user = f.user()
    inactive = f.user(is_active=False)
    bodies = set()
    for identifier, password in [(user.username, "wrong-password-123"), ("nobody-here", f.PASSWORD),
                                 (inactive.username, f.PASSWORD)]:
        response = post_login(client, identifier, password)
        assert response.status_code == 200
        assert "_auth_user_id" not in client.session
        content = response.content.decode()
        assert "Invalid credentials" in content
        bodies.add(content.split('name="csrfmiddlewaretoken" value="')[0])
    assert len(bodies) == 1  # same page apart from the CSRF token


def test_failed_login_records_hashed_identifier_never_password(client):
    post_login(client, "someone@example.test", "Sup3r-Secret-Guess")
    event = SecurityEvent.objects.get(event_type=SecurityEventType.LOGIN_FAILURE)
    assert "someone" not in event.identifier_hash and "Sup3r" not in str(event.details)


def test_session_key_rotates_on_login(client):
    user = f.user()
    client.get(LOGIN)
    client.session.save()
    before = client.session.session_key
    post_login(client, user.username)
    assert client.session.session_key != before
    assert UserSession.objects.filter(user=user, session_key=client.session.session_key).exists()


@pytest.mark.parametrize("target", ["https://evil.example.com/x", "//evil.example.com", "/\\evil.example.com",
                                    "javascript:alert(1)"])
def test_next_parameter_cannot_redirect_off_site(client, target):
    user = f.user()
    response = post_login(client, user.username, next=target)
    assert response.status_code == 302 and response.url == reverse("core:dashboard")


def test_next_parameter_allows_local_path(client):
    user = f.user()
    response = post_login(client, user.username, next="/accounts/profile/")
    assert response.url == "/accounts/profile/"


def test_logout_requires_post_and_flushes_session(client):
    user = f.user()
    post_login(client, user.username)
    assert client.get(reverse("accounts:logout")).status_code == 405
    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302 and "_auth_user_id" not in client.session
    assert not UserSession.objects.filter(user=user).exists()


def test_successful_login_sets_trusted_device_cookie(client, settings):
    user = f.user()
    response = post_login(client, user.username)
    cookie = response.cookies[settings.DEVICE_COOKIE_NAME]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).count() == 1


def test_protected_page_redirects_anonymous_to_login(client):
    response = client.get(reverse("accounts:profile"))
    assert response.status_code == 302 and response.url.startswith(LOGIN)


def test_password_field_is_never_echoed_back(client):
    response = post_login(client, "nobody", "Visible-Password-Value-1")
    assert b"Visible-Password-Value-1" not in response.content

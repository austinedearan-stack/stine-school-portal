"""Brute force and lockout resistance (ARCHITECTURE.md §4.1, D7, D17; audit A-5, A-6)."""

from unittest import mock

import pytest
from django.core.cache import cache

from apps.accounts import throttle
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


def attempt(client, identifier, password="wrong-password-xyz", ip="203.0.113.10"):
    return client.post(LOGIN, {"identifier": identifier, "password": password}, REMOTE_ADDR=ip)


def test_five_failures_lock_that_identifier_from_that_ip_even_with_the_right_password(client):
    user = f.user(Role.STUDENT)
    for _ in range(throttle.PAIR_THRESHOLD):
        assert attempt(client, user.username).status_code == 200
    response = attempt(client, user.username, f.PASSWORD)
    assert response.status_code == 429 and "_auth_user_id" not in client.session
    assert SecurityEvent.objects.filter(event_type=SecurityEventType.THROTTLED).exists()


def test_lock_is_per_ip_so_attacker_cannot_lock_out_the_owner_elsewhere(client):
    user = f.user(Role.STUDENT)
    for _ in range(throttle.PAIR_THRESHOLD):
        attempt(client, user.username, ip="198.51.100.66")
    response = attempt(client, user.username, f.PASSWORD, ip="203.0.113.20")
    assert response.status_code == 302


def test_unknown_identifier_is_throttled_identically(client):
    for _ in range(throttle.PAIR_THRESHOLD):
        assert attempt(client, "no-such-user").status_code == 200
    assert attempt(client, "no-such-user").status_code == 429


def test_owner_with_device_cookie_is_not_locked_out_by_an_attacker(client, settings):
    user = f.user(Role.STUDENT)
    first = attempt(client, user.username, f.PASSWORD, ip="203.0.113.30")  # owner logs in once
    device_cookie = first.cookies[settings.DEVICE_COOKIE_NAME].value
    client.post("/accounts/logout/")

    attacker = client.__class__()
    for i in range(40):  # distributed attack: exceeds the per-subject hourly budget
        attempt(attacker, user.username, ip=f"198.51.100.{i}")
    assert attempt(attacker, user.username, f.PASSWORD, ip="198.51.100.99").status_code == 429

    owner = client.__class__()
    owner.cookies[settings.DEVICE_COOKIE_NAME] = device_cookie
    assert attempt(owner, user.username, f.PASSWORD, ip="198.51.100.99").status_code == 302


def test_per_subject_budget_slows_down_distributed_guessing(client):
    user = f.user(Role.STUDENT)
    for i in range(throttle.SUBJECT_LIMIT):
        attempt(client, user.username, ip=f"203.0.113.{100 + i}")  # 30 different IPs, 1 failure each
    assert attempt(client, user.username, ip="203.0.113.250").status_code == 200  # the one slot this minute
    assert attempt(client, user.username, ip="203.0.113.251").status_code == 429


def test_ip_budget_blocks_spraying_many_accounts(client):
    for i in range(throttle.IP_LIMIT):
        attempt(client, f"user-{i}")
    assert attempt(client, "user-final").status_code == 429


def test_backoff_doubles_and_is_capped():
    from apps.core.ratelimit import backoff_seconds

    assert [backoff_seconds(n, threshold=5) for n in (4, 5, 6, 7)] == [0, 60, 120, 240]
    assert backoff_seconds(50, threshold=5) == 1800


def test_limiter_failure_fails_closed(client):
    user = f.user()
    with mock.patch("apps.core.ratelimit.cache") as broken:
        broken.get.side_effect = ConnectionError("redis down")
        broken.add.side_effect = ConnectionError("redis down")
        response = attempt(client, user.username, f.PASSWORD)
    assert response.status_code == 503 and "_auth_user_id" not in client.session

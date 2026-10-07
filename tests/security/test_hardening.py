"""Phase 12 hardening: body limits, unsafe-method throttle, headers, request ids, audit seals, URL sweep."""

import logging
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test import override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.urls.converters import UUIDConverter
from django.utils import timezone

from apps.core.audit import record_audit_event, record_security_event
from apps.core.models import AuditLog, AuditSeal, SecurityEventType
from apps.core.seals import seal_all, verify_all
from tests import factories as f
from tests.helpers import login

pytestmark = pytest.mark.django_db


# --- Body size and throttling ---------------------------------------------------------------------


def test_oversized_request_is_refused_before_reading(client, settings):
    response = client.post(reverse("accounts:login"), data="x", content_type="text/plain",
                           CONTENT_LENGTH=str(settings.MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413


@override_settings(UNSAFE_REQUESTS_PER_MINUTE=3)
def test_unsafe_method_throttle(client):
    from django.core.cache import cache

    cache.clear()
    student = f.student()
    login(client, student.user)
    statuses = [client.post(reverse("accounts:photo_remove")).status_code for _ in range(4)]
    assert statuses[:3] == [302, 302, 302] and statuses[3] == 429
    assert client.get(reverse("core:dashboard")).status_code == 200  # reads are not throttled
    cache.clear()


# --- Headers ---------------------------------------------------------------------------------------


def test_strict_csp_and_headers(client):
    response = client.get(reverse("accounts:login"))
    csp = response["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp and "http:" not in csp
    for directive in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'", "form-action 'self'",
                      "upgrade-insecure-requests"):
        assert directive in csp
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["X-Frame-Options"] == "DENY"
    assert response["Referrer-Policy"] == "same-origin"
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "camera=()" in response["Permissions-Policy"]
    assert len(response["X-Request-ID"]) == 32


def test_forged_request_id_is_replaced(client):
    response = client.get("/healthz", HTTP_X_REQUEST_ID="<script>alert(1)</script>")
    assert "<" not in response["X-Request-ID"]


def test_security_txt_and_robots(client):
    body = client.get("/.well-known/security.txt").content.decode()
    assert body.startswith("Contact: ") and "Expires: " in body
    assert "Disallow: /" in client.get("/robots.txt").content.decode()


def test_log_records_carry_the_request_id():
    from apps.core.logging import RequestIdFilter
    from apps.core.middleware import CURRENT_REQUEST_ID

    token = CURRENT_REQUEST_ID.set("abc123")
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
        RequestIdFilter().filter(record)
        assert record.request_id == "abc123"
    finally:
        CURRENT_REQUEST_ID.reset(token)


# --- Audit seals -----------------------------------------------------------------------------------


@pytest.fixture
def sealed():
    for i in range(5):
        record_audit_event(None, f"TEST.EVENT_{i}")
    record_security_event(SecurityEventType.LOGIN_FAILURE, identifier="someone")
    later = timezone.now() + timedelta(hours=1)
    seals = seal_all(now=later)
    assert {s.stream for s in seals} >= {"AUDIT", "SECURITY"}
    return later


def test_fresh_seals_verify(sealed):
    assert verify_all() == []


def test_second_seal_chains_to_the_first(sealed):
    record_audit_event(None, "TEST.LATER")
    seal_all(now=sealed + timedelta(hours=1))
    seals = list(AuditSeal.objects.filter(stream="AUDIT").order_by("from_seq"))
    assert len(seals) == 2 and seals[1].previous_sha256 == seals[0].sha256
    assert verify_all() == []


def test_recent_rows_wait_for_the_grace_window():
    record_audit_event(None, "TEST.TOO_NEW")
    assert seal_all() == []  # younger than 15 minutes: not sealed yet


def _bypass_triggers():
    """Simulate a DBA who disables the append-only triggers (SQLite test database)."""
    if connection.vendor != "sqlite":
        pytest.skip("trigger names differ on PostgreSQL; covered by the SQLite run")
    with connection.cursor() as cursor:
        cursor.execute("DROP TRIGGER IF EXISTS core_auditlog_no_update")
        cursor.execute("DROP TRIGGER IF EXISTS core_auditlog_no_delete")


def test_edited_row_is_detected(sealed):
    _bypass_triggers()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE core_auditlog SET action = 'COVER.UP' WHERE action = 'TEST.EVENT_2'")
    assert any("hash mismatch" in p for p in verify_all())


def test_deleted_row_is_detected(sealed):
    _bypass_triggers()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM core_auditlog WHERE action = 'TEST.EVENT_3'")
    assert any("rows deleted or inserted" in p for p in verify_all())


def test_forged_row_reusing_a_sealed_seq_is_detected(sealed):
    """Delete a sealed row and insert a forged one with the same seq: counts match, the hash does not."""
    _bypass_triggers()
    victim = AuditLog.objects.get(action="TEST.EVENT_3")
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM core_auditlog WHERE id = %s", [victim.pk.hex])
        cursor.execute(
            "INSERT INTO core_auditlog (id, seq, timestamp, actor_identifier, actor_role, action, object_type, "
            "object_id, changes, user_agent, request_id, outcome) VALUES (%s, %s, %s, 'SYSTEM', '', 'FORGED', '', "
            "'', '{}', '', '', 'SUCCESS')", [uuid.uuid4().hex, victim.seq, victim.timestamp])
    problems = verify_all()
    assert any("hash mismatch" in p for p in problems)
    assert not any("rows deleted or inserted" in p for p in problems)


# --- URL protection sweep -------------------------------------------------------------------------

PUBLIC = {"health", "security_txt", "robots_txt", "accounts:login", "accounts:password_reset_request",
          "accounts:password_reset_confirm", "accounts:mfa_verify", "accounts:mfa_enroll", "accounts:logout"}


def _named_patterns(patterns=None, namespace=""):
    for pattern in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(pattern, URLResolver):
            ns = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
            yield from _named_patterns(pattern.url_patterns, ns)
        elif isinstance(pattern, URLPattern) and pattern.name:
            yield f"{namespace}{pattern.name}", pattern


def _url(name, pattern):
    kwargs = {}
    for key, converter in pattern.pattern.converters.items():
        kwargs[key] = uuid.uuid4() if isinstance(converter, UUIDConverter) else "x"
    return reverse(name, kwargs=kwargs)


def test_every_non_public_url_requires_login(client):
    checked = 0
    for name, pattern in _named_patterns():
        if name in PUBLIC:
            continue
        response = client.get(_url(name, pattern))
        checked += 1
        assert response.status_code in (302, 405), f"{name} answered {response.status_code} to an anonymous user"
        if response.status_code == 302:
            assert response.url.startswith("/accounts/login/"), name
    assert checked > 60


ADMIN_PREFIXES = ("administration:", "timetable:manage", "timetable:entry", "timetable:venue", "hostels:manage",
                  "hostels:application", "hostels:direct_offer", "hostels:transfer", "hostels:vacate", "hostels:hostel_",
                  "hostels:space_status", "hostels:window", "clubs:manage", "clubs:create", "requests:queue",
                  "requests:categor", "notifications:manage", "notifications:announcement_create")


def test_students_are_refused_every_administrative_page(client):
    login(client, f.student().user)
    for name, pattern in _named_patterns():
        if name.startswith(ADMIN_PREFIXES):
            status = client.get(_url(name, pattern)).status_code
            assert status in (403, 404, 405), f"{name} answered {status} to a student"

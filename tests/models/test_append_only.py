"""Phase 2 exit criterion: audit tables are append-only at the ORM and the database layer (audit I-5)."""

import pytest
from django.db import DatabaseError, connection, transaction

from apps.core.audit import record_audit_event, record_security_event
from apps.core.models import AppendOnlyError, AuditLog, SecurityEvent, SecurityEventType
from apps.student_requests.models import RequestCategory, RequestStatusChange, StudentRequest
from tests import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def audit_row():
    return record_audit_event(f.user(), "TEST.ACTION", object_type="Thing", object_id="1",
                              changes={"before": {"a": 1}, "after": {"a": 2}})


def _strict_session():
    """On PostgreSQL the test connection may run in maintenance mode; turn it off for this test."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL portal.audit_maintenance = 'off'")


def test_orm_refuses_update_of_saved_row(audit_row):
    audit_row.action = "TAMPERED"
    with pytest.raises(AppendOnlyError):
        audit_row.save()


def test_orm_refuses_delete(audit_row):
    with pytest.raises(AppendOnlyError):
        audit_row.delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.filter(pk=audit_row.pk).delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.all().update(action="TAMPERED")
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.bulk_update([audit_row], ["action"])


@pytest.mark.parametrize("table", ["core_auditlog", "core_securityevent", "student_requests_requeststatuschange"])
def test_database_trigger_refuses_raw_update_and_delete(table, audit_row):
    record_security_event(SecurityEventType.LOGIN_FAILURE, identifier="someone")
    student = f.student()
    req = StudentRequest.objects.create(number="REQ-2026-000009", student=student,
                                        category=RequestCategory.objects.get(code="GENERAL"),
                                        subject="s", description="d")
    RequestStatusChange.objects.create(request=req, to_status="SUBMITTED")
    _strict_session()
    column = "action" if table == "core_auditlog" else ("event_type" if table == "core_securityevent" else "note")
    for statement in (f"UPDATE {table} SET {column} = 'x'", f"DELETE FROM {table}"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement)


@pytest.mark.postgres
def test_truncate_is_refused_on_postgres(audit_row):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL only")
    _strict_session()
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("TRUNCATE core_auditlog CASCADE")


def test_seq_is_monotonic(audit_row):
    second = record_audit_event(None, "TEST.SECOND")
    rows = list(AuditLog.objects.order_by("timestamp", "seq").values_list("seq", flat=True))
    if connection.vendor != "postgresql":  # on PostgreSQL the trigger assigns it inside the DB
        assert second.seq > audit_row.seq
    assert len(set(rows)) == len(rows) and all(s > 0 for s in rows)


def test_secret_like_keys_are_redacted_in_audit_changes():
    row = record_audit_event(None, "TEST.REDACT", changes={"password": "hunter2", "after": {"otp_code": "123456"}})
    row = AuditLog.objects.get(pk=row.pk)
    assert "hunter2" not in str(row.changes) and "123456" not in str(row.changes)


def test_security_event_stores_identifier_hash_not_identifier():
    record_security_event(SecurityEventType.LOGIN_FAILURE, identifier="Alice@Example.test")
    event = SecurityEvent.objects.get(event_type=SecurityEventType.LOGIN_FAILURE)
    assert event.identifier_hash and "alice" not in event.identifier_hash.lower()
    assert len(event.identifier_hash) == 64

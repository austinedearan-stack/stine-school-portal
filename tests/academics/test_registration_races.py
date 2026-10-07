"""Concurrency: simultaneous registrations cannot exceed capacity or the credit limit, or duplicate
(spec §32; ARCHITECTURE.md T7). Real threads, real PostgreSQL row locks; skipped on SQLite."""

import threading

import pytest
from django.db import connection

from apps.academics import services
from apps.academics.models import RegistrationStatus, UnitRegistration
from apps.core.context import SYSTEM
from tests import factories as f

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def run_concurrently(calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def worker(index, fn):
        try:
            barrier.wait(timeout=10)
            results[index] = fn()
        except Exception as exc:  # noqa: BLE001 - collected for assertions
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_last_seat_goes_to_exactly_one_student():
    semester = f.semester(is_current=True)
    offering = f.offering(semester=semester, capacity=1)
    students = [f.student() for _ in range(6)]
    results = run_concurrently([lambda s=s: services.register(s.user, s, offering, SYSTEM) for s in students])
    successes = [r for r in results if isinstance(r, UnitRegistration)]
    assert len(successes) == 1
    assert all(isinstance(r, services.RegistrationError) for r in results if r not in successes)
    assert UnitRegistration.objects.filter(offering=offering, status=RegistrationStatus.REGISTERED).count() == 1


def test_parallel_registrations_cannot_exceed_credit_limit():
    semester = f.semester(is_current=True)
    student = f.student(program=f.program(max_credits_per_semester=4))
    offerings = [f.offering(semester=semester, unit=f.unit(credit_hours=3)) for _ in range(4)]
    results = run_concurrently([lambda o=o: services.register(student.user, student, o, SYSTEM) for o in offerings])
    assert sum(isinstance(r, UnitRegistration) for r in results) == 1


def test_double_submit_creates_one_registration():
    semester = f.semester(is_current=True)
    student = f.student()
    offering = f.offering(semester=semester)
    run_concurrently([lambda: services.register(student.user, student, offering, SYSTEM)] * 5)
    assert UnitRegistration.objects.filter(student=student, offering=offering).count() == 1

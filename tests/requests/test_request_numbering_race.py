"""Concurrent submissions get unique, gap-free numbers (audit F-4). PostgreSQL only."""

import pytest

from apps.core.context import SYSTEM
from apps.student_requests import services
from apps.student_requests.models import RequestCategory, StudentRequest
from tests import factories as f
from tests.academics.test_registration_races import run_concurrently

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def test_parallel_submissions_get_distinct_sequential_numbers():
    category = RequestCategory.objects.get(code="GENERAL")
    students = [f.student() for _ in range(8)]
    run_concurrently([lambda s=s: services.submit(s.user, s, category, "x", "y", SYSTEM) for s in students])
    numbers = sorted(StudentRequest.objects.values_list("number", flat=True))
    assert len(numbers) == 8 == len(set(numbers))
    assert [int(n.rsplit("-", 1)[1]) for n in numbers] == list(range(1, 9))

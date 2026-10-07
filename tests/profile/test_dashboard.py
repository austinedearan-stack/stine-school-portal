"""Dashboards show only the signed-in user's data (Phase 4; spec §5–6)."""

import pytest
from django.urls import reverse

from apps.academics.models import UnitRegistration
from apps.core.capabilities import Role
from apps.notifications.services import notify
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


def register(student, offering):
    return UnitRegistration.objects.create(student=student, offering=offering, unit=offering.unit,
                                           semester=offering.semester)


@pytest.fixture
def semester():
    return f.semester(is_current=True)


def test_student_dashboard_shows_own_units_only(client, semester):
    me, other = f.student(), f.student()
    mine = f.offering(semester=semester, unit=f.unit(title="Quantum Basket Weaving"))
    theirs = f.offering(semester=semester, unit=f.unit(title="Secret Other Unit"))
    register(me, mine)
    register(other, theirs)
    notify(other.user, "SYSTEM", "Other user's private notice")
    login(client, me.user)
    # A query parameter can never switch whose dashboard is shown.
    response = client.get(reverse("core:dashboard"), {"student": str(other.pk), "user": str(other.user.pk)})
    content = response.content.decode()
    assert "Quantum Basket Weaving" in content
    assert "Secret Other Unit" not in content and "Other user's private notice" not in content


def test_lecturer_dashboard_lists_teaching(client, semester):
    lecturer = f.staff()
    f.offering(semester=semester, lecturer=lecturer, unit=f.unit(title="Applied Widgets"))
    login(client, lecturer.user)
    assert b"Applied Widgets" in client.get(reverse("core:dashboard")).content


def test_statistics_need_the_capability(client, semester):
    plain = f.user(Role.ADMIN)
    enrol(plain)
    login(client, plain)
    assert b"Active students" not in client.get(reverse("core:dashboard")).content
    auditor = f.user(Role.ADMIN, groups=["Auditor"])
    enrol(auditor)
    login(client, auditor)
    assert b"Active students" in client.get(reverse("core:dashboard")).content


def test_navigation_hides_links_the_user_cannot_use(client, semester):
    student = f.student()
    login(client, student.user)
    content = client.get(reverse("core:dashboard")).content.decode()
    assert "Audit log" not in content and "Admin dashboard" not in content

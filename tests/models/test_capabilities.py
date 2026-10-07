"""Phase 2 exit criterion: capability catalog migration and role ceilings (ARCHITECTURE.md §5.2)."""

import pytest
from django.contrib.auth.models import Group, Permission

from apps.core.authz import has_capability
from apps.core.capabilities import CAPABILITIES, SUPERADMIN_GROUP, Role, default_groups
from apps.core.models import PortalCapability
from tests import factories as f

pytestmark = pytest.mark.django_db


def test_model_permissions_match_the_catalog():
    assert {code for code, _ in PortalCapability._meta.permissions} == set(CAPABILITIES)


def test_catalog_permissions_and_default_groups_are_seeded():
    assert set(Permission.objects.filter(content_type__app_label="core").values_list("codename", flat=True)) >= set(
        CAPABILITIES
    )
    for name, codenames in default_groups().items():
        group = Group.objects.get(name=name)
        assert set(group.permissions.values_list("codename", flat=True)) == codenames


def test_superadmin_only_capabilities_are_only_in_the_superadmin_group():
    for codename in ("manage_roles", "manage_system_settings", "manage_backups"):
        assert CAPABILITIES[codename].ceiling == {Role.SUPERADMIN}
        holders = set(Group.objects.filter(permissions__codename=codename).values_list("name", flat=True))
        assert holders == {SUPERADMIN_GROUP}


def test_capability_requires_role_within_ceiling():
    admin = f.user(Role.ADMIN, groups=["Superadmin"])  # misconfigured: admin placed in Superadmin group
    assert not has_capability(admin, "manage_roles")  # ceiling = SUPERADMIN only
    assert has_capability(admin, "manage_backups") is False
    superadmin = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    assert has_capability(superadmin, "manage_roles")


def test_student_never_holds_capabilities_even_if_granted():
    student = f.user(Role.STUDENT, groups=["Registrar", "Auditor"])
    assert not any(has_capability(student, codename) for codename in CAPABILITIES)


def test_inactive_user_holds_nothing():
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    assert has_capability(admin, "view_audit_logs")
    admin.is_active = False
    admin.save()
    assert not has_capability(admin, "view_audit_logs")


def test_record_grades_is_staff_only():
    assert CAPABILITIES["record_grades"].ceiling == {Role.STAFF}
    lecturer = f.user(Role.STAFF, groups=["Lecturers"])
    assert has_capability(lecturer, "record_grades")

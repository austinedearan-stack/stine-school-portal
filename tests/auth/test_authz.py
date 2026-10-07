"""Authorization layer and account-administration invariants (ARCHITECTURE.md §5; audit Z-6, T6)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import RequestFactory

from apps.accounts import admin_services, mfa
from apps.core.authz import authorize, has_capability
from apps.core.authz.decorators import capability_required, role_required
from apps.core.capabilities import Role
from apps.core.context import SYSTEM
from apps.core.models import AuditLog, Outcome, SecurityEvent, SecurityEventType
from tests import factories as f

pytestmark = pytest.mark.django_db


def test_authorize_denies_and_records_the_denial():
    student = f.user(Role.STUDENT)
    with pytest.raises(PermissionDenied):
        authorize(student, "can_view_audit_logs")
    assert SecurityEvent.objects.filter(event_type=SecurityEventType.PERMISSION_DENIED, user=student).exists()
    assert AuditLog.objects.filter(actor=student, outcome=Outcome.DENIED, action="DENIED.can_view_audit_logs").exists()


def test_unknown_action_is_a_programming_error():
    with pytest.raises(KeyError):
        authorize(f.user(Role.ADMIN), "can_do_anything_at_all")


def test_inactive_user_is_always_denied():
    auditor = f.user(Role.ADMIN, groups=["Auditor"], is_active=False)
    with pytest.raises(PermissionDenied):
        authorize(auditor, "can_view_audit_logs")


def test_decorators_return_403_for_wrong_role_or_missing_capability():
    view = role_required(Role.STUDENT)(lambda request: HttpResponse("ok"))
    request = RequestFactory().get("/x")
    request.user = f.user(Role.STAFF)
    with pytest.raises(PermissionDenied):
        view(request)
    gated = capability_required("view_audit_logs")(lambda request: HttpResponse("ok"))
    request.user = f.user(Role.ADMIN)  # admin without the capability (Z-6: no blanket admin powers)
    with pytest.raises(PermissionDenied):
        gated(request)
    request.user = f.user(Role.ADMIN, groups=["Auditor"])
    assert gated(request).status_code == 200


def test_anonymous_is_redirected_to_login_by_decorators():
    from django.contrib.auth.models import AnonymousUser

    view = capability_required("view_audit_logs")(lambda request: HttpResponse("ok"))
    request = RequestFactory().get("/admin-thing/")
    request.user = AnonymousUser()
    response = view(request)
    assert response.status_code == 302 and "/accounts/login/" in response.url


# --- Account administration invariants --------------------------------------------------------------


@pytest.fixture
def superadmin():
    return f.user(Role.SUPERADMIN, groups=["Superadmin"])


def test_admin_cannot_manage_roles_even_if_placed_in_superadmin_group():
    admin = f.user(Role.ADMIN, groups=["Superadmin"])
    target = f.user(Role.STUDENT)
    with pytest.raises(PermissionDenied):
        admin_services.change_role(admin, target, Role.ADMIN, SYSTEM)


def test_nobody_changes_their_own_role(superadmin):
    with pytest.raises(PermissionDenied):
        admin_services.change_role(superadmin, superadmin, Role.ADMIN, SYSTEM)


def test_role_change_strips_all_grants(superadmin):
    staff = f.user(Role.STAFF, groups=["Timetabling"])
    admin_services.change_role(superadmin, staff, Role.ADMIN, SYSTEM)
    staff.refresh_from_db()
    assert staff.role == Role.ADMIN and staff.groups.count() == 0
    assert AuditLog.objects.filter(action="ACCOUNT.ROLE_CHANGED", object_id=str(staff.pk)).exists()


def test_group_outside_role_ceiling_is_refused(superadmin):
    staff = f.user(Role.STAFF)
    with pytest.raises(ValidationError):
        admin_services.set_groups(superadmin, staff, ["Registrar"], SYSTEM)  # manage_students: ADMIN+ only
    admin_services.set_groups(superadmin, staff, ["Timetabling"], SYSTEM)
    assert has_capability(staff, "manage_timetable")


def test_last_superadmin_cannot_be_removed(superadmin):
    other = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    admin_services.change_role(superadmin, other, Role.ADMIN, SYSTEM)  # fine: one remains
    other.refresh_from_db()
    assert other.role == Role.ADMIN
    third = f.user(Role.SUPERADMIN)  # a superadmin WITHOUT manage_roles does not count
    with pytest.raises(PermissionDenied):
        admin_services.change_role(third, superadmin, Role.ADMIN, SYSTEM)


def test_demoting_the_only_capable_superadmin_is_refused():
    a = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    b = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    admin_services.change_role(a, b, Role.ADMIN, SYSTEM)
    b.refresh_from_db()
    # b is now ADMIN; a is the only one left; nobody else can demote a, and a cannot demote itself.
    with pytest.raises(PermissionDenied):
        admin_services.change_role(a, a, Role.ADMIN, SYSTEM)


def test_it_support_cannot_manage_mfa_required_accounts():
    it_support = f.user(Role.ADMIN, groups=["IT Support"])
    student = f.user(Role.STUDENT)
    privileged_staff = f.user(Role.STAFF, groups=["Timetabling"])
    admin_services.set_active(it_support, student, False, SYSTEM)
    student.refresh_from_db()
    assert not student.is_active
    with pytest.raises(PermissionDenied):
        admin_services.set_active(it_support, privileged_staff, False, SYSTEM)
    with pytest.raises(PermissionDenied):
        admin_services.reset_mfa(it_support, f.user(Role.ADMIN), SYSTEM)


def test_mfa_reset_issues_enrollment_code_and_removes_device(superadmin):
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    from tests.helpers import enrol

    enrol(admin)
    code = admin_services.reset_mfa(superadmin, admin, SYSTEM)
    assert mfa.confirmed_device(admin) is None
    assert mfa.check_enrollment_code(admin, code)


def test_deactivation_ends_sessions(client, superadmin):
    from django.urls import reverse

    from tests.helpers import login

    student = f.user(Role.STUDENT)
    client.post(reverse("accounts:login"), {"identifier": student.username, "password": f.PASSWORD})
    assert client.get(reverse("core:dashboard")).status_code == 200
    admin_services.set_active(superadmin, student, False, SYSTEM)
    assert client.get(reverse("core:dashboard")).status_code == 302
    assert login  # helper import kept for symmetry with other suites



def test_last_capable_superadmin_cannot_be_deactivated_or_stripped():
    a = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    b = f.user(Role.SUPERADMIN, groups=["Superadmin"])
    admin_services.set_groups(a, b, [], SYSTEM)  # allowed: a still holds manage_roles
    with pytest.raises(PermissionDenied):
        admin_services.set_active(b, a, False, SYSTEM)  # b lost manage_roles, so it cannot act on a
    # With a as the only capable superadmin, any change that would leave none is refused.
    from apps.accounts.admin_services import _assert_superadmin_remains

    a.groups.clear()
    with pytest.raises(ValidationError):
        _assert_superadmin_remains(True)

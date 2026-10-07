"""Account administration services with the invariants of ARCHITECTURE.md §5.2.

* Nobody changes their own role, groups, capabilities or activation.
* A role change strips all groups and direct permissions (no dormant grants activate on promotion).
* A capability outside the target's role ceiling is refused at grant time (and ignored at check time).
* At least one active SUPERADMIN holding manage_roles always remains: every such change locks all
  active SUPERADMIN rows (ordered by id) and re-checks the invariant in the same transaction.
* Admins never set another user's password: they send the user a reset code and force a change.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts import devices, mfa, passwords, sessions
from apps.accounts.models import User
from apps.core.audit import record_audit_event, record_security_event
from apps.core.authz import authorize, has_capability
from apps.core.capabilities import CAPABILITIES, Role, default_groups, role_may_hold
from apps.core.context import RequestContext
from apps.core.models import SecurityEventType
from apps.notifications.services import notify


def _lock_superadmins_then(target: User) -> User:
    list(User.objects.select_for_update().filter(role=Role.SUPERADMIN, is_active=True).order_by("pk"))
    return User.objects.select_for_update().get(pk=target.pk)


def _assert_superadmin_remains(target_was_superadmin: bool) -> None:
    """Only a change to a superadmin's account can break the invariant, so only those are checked."""
    if not target_was_superadmin:
        return
    remaining = [
        u for u in User.objects.filter(role=Role.SUPERADMIN, is_active=True)
        if has_capability(u, "manage_roles")
    ]
    if not remaining:
        raise ValidationError("At least one active superadmin holding manage_roles must remain.")


def group_capabilities(group: Group) -> set[str]:
    return set(group.permissions.filter(content_type__app_label="core").values_list("codename", flat=True))


def assignable_groups(role: str) -> list[Group]:
    """Groups whose every capability is within the role's ceiling."""
    result = []
    for group in Group.objects.prefetch_related("permissions").order_by("name"):
        caps = group_capabilities(group)
        if caps and all(role_may_hold(role, c) for c in caps):
            result.append(group)
    return result


@transaction.atomic
def change_role(actor: User, target: User, new_role: str, ctx: RequestContext) -> User:
    authorize(actor, "can_change_roles", target, ctx=ctx)
    if new_role not in Role.ALL:
        raise ValidationError("Unknown role.")
    target = _lock_superadmins_then(target)
    old_role = target.role
    was_superadmin = old_role == Role.SUPERADMIN
    if old_role == new_role:
        return target
    old_groups = sorted(target.groups.values_list("name", flat=True))
    target.role = new_role
    target.save(update_fields=["role", "updated_at"])
    target.groups.clear()
    target.user_permissions.clear()
    _assert_superadmin_remains(was_superadmin)
    record_audit_event(actor, "ACCOUNT.ROLE_CHANGED", target, ctx=ctx, changes={
        "before": {"role": old_role, "groups": old_groups}, "after": {"role": new_role, "groups": []}})
    record_security_event(SecurityEventType.ROLE_CHANGED, ctx=ctx, user=target,
                          details={"by": str(actor.pk), "from": old_role, "to": new_role})
    notify(target, "SECURITY", "Your account role changed", f"Your role is now {target.get_role_display()}.")
    return target


@transaction.atomic
def set_groups(actor: User, target: User, group_names: list[str], ctx: RequestContext) -> User:
    authorize(actor, "can_change_roles", target, ctx=ctx)
    target = _lock_superadmins_then(target)
    groups = list(Group.objects.filter(name__in=group_names))
    if len(groups) != len(set(group_names)):
        raise ValidationError("Unknown group.")
    for group in groups:
        outside = [c for c in group_capabilities(group) if not role_may_hold(target.role, c)]
        if outside:
            raise ValidationError(f"Group {group.name} grants capabilities outside the {target.role} role: "
                                  f"{', '.join(sorted(outside))}.")
    before = sorted(target.groups.values_list("name", flat=True))
    target.groups.set(groups)
    target.user_permissions.clear()  # capabilities are granted through groups only
    _assert_superadmin_remains(target.role == Role.SUPERADMIN)
    after = sorted(g.name for g in groups)
    record_audit_event(actor, "ACCOUNT.GROUPS_CHANGED", target, ctx=ctx,
                       changes={"before": {"groups": before}, "after": {"groups": after}})
    record_security_event(SecurityEventType.ROLE_CHANGED, ctx=ctx, user=target,
                          details={"by": str(actor.pk), "groups": after})
    notify(target, "SECURITY", "Your access changed", "Your account permissions were updated by an administrator.")
    return target


@transaction.atomic
def set_active(actor: User, target: User, active: bool, ctx: RequestContext) -> User:
    authorize(actor, "can_manage_user_account", target, ctx=ctx)
    target = _lock_superadmins_then(target)
    if target.is_active == active:
        return target
    target.is_active = active
    target.save(update_fields=["is_active", "updated_at"])
    if not active:
        devices.revoke_all(target)
        _end_sessions(target)
    _assert_superadmin_remains(target.role == Role.SUPERADMIN)
    record_audit_event(actor, "ACCOUNT.ACTIVATED" if active else "ACCOUNT.DEACTIVATED", target, ctx=ctx,
                       changes={"before": {"is_active": not active}, "after": {"is_active": active}})
    return target


@transaction.atomic
def reset_mfa(actor: User, target: User, ctx: RequestContext) -> str:
    """After out-of-band identity verification: remove devices, end sessions, issue a new enrollment code."""
    authorize(actor, "can_manage_user_account", target, ctx=ctx)
    mfa.remove_devices(target)
    _end_sessions(target)
    code = mfa.issue_enrollment_code(target, issued_by=actor)
    record_audit_event(actor, "ACCOUNT.MFA_RESET", target, ctx=ctx)
    record_security_event(SecurityEventType.MFA_RESET, ctx=ctx, user=target, details={"by": str(actor.pk)})
    notify(target, "SECURITY", "Your two-step verification was reset",
           "An administrator reset your authenticator. You will need a new enrollment code to sign in.")
    return code


@transaction.atomic
def issue_enrollment_code(actor: User, target: User, ctx: RequestContext) -> str:
    authorize(actor, "can_manage_user_account", target, ctx=ctx)
    if mfa.confirmed_device(target):
        raise ValidationError("This account already has an authenticator; reset MFA instead.")
    code = mfa.issue_enrollment_code(target, issued_by=actor)
    record_audit_event(actor, "ACCOUNT.MFA_ENROLLMENT_CODE_ISSUED", target, ctx=ctx)
    return code


@transaction.atomic
def force_password_reset(actor: User, target: User, ctx: RequestContext) -> None:
    authorize(actor, "can_manage_user_account", target, ctx=ctx)
    target.must_change_password = True
    target.save(update_fields=["must_change_password", "updated_at"])
    passwords.request_reset(target, ctx)
    record_audit_event(actor, "ACCOUNT.PASSWORD_RESET_SENT", target, ctx=ctx)


def _end_sessions(user: User) -> None:
    sessions.end_all_sessions(user)


def default_group_names() -> list[str]:
    return sorted(default_groups())


def capability_names() -> list[str]:
    return sorted(CAPABILITIES)

"""Named policy functions (ARCHITECTURE.md §5.1). One module, one place to review.

Each policy is ``fn(actor, obj) -> bool`` and is registered under its action name. Policies may
assume ``actor`` is authenticated and active (``authorize`` checks that first). Feature phases add
their object policies here; models are imported lazily to keep this module dependency-free.
"""

from __future__ import annotations

from collections.abc import Callable

from apps.core.authz import has_capability
from apps.core.capabilities import Role

POLICIES: dict[str, Callable] = {}


def policy(name: str):
    def register(fn):
        if name in POLICIES:
            raise RuntimeError(f"duplicate policy {name}")
        POLICIES[name] = fn
        return fn

    return register


def capability_policy(name: str, codename: str):
    POLICIES[name] = lambda actor, obj=None: has_capability(actor, codename)


# --- Pure capability policies ---------------------------------------------------------------------
for _name, _cap in [
    ("can_manage_students", "manage_students"),
    ("can_manage_staff", "manage_staff"),
    ("can_manage_academics", "manage_academics"),
    ("can_manage_units", "manage_units"),
    ("can_manage_timetable", "manage_timetable"),
    ("can_manage_hostels", "manage_hostels"),
    ("can_manage_request_config", "manage_request_config"),
    ("can_manage_clubs_admin", "manage_clubs"),
    ("can_view_audit_logs", "view_audit_logs"),
    ("can_view_statistics", "view_statistics"),
    ("can_manage_roles", "manage_roles"),
    ("can_manage_system", "manage_system_settings"),
    ("can_manage_backups", "manage_backups"),
    ("can_manage_grades", "manage_grades"),
    ("can_execute_transfers", "execute_transfers"),
    ("can_approve_transfers", "approve_transfers"),
]:
    capability_policy(_name, _cap)


# --- Accounts -------------------------------------------------------------------------------------


@policy("can_manage_user_account")
def can_manage_user_account(actor, target) -> bool:
    """Activate/deactivate, send a reset code, reset MFA (§5.3 rows "Manage STUDENT/STAFF accounts").

    Accounts that are MFA-required (admins, superadmins, staff holding capabilities) can only be
    managed by holders of manage_roles; nobody manages their own account through these actions.
    """
    from apps.accounts.mfa import is_mfa_required

    if target is None:
        return has_capability(actor, "manage_user_accounts") or has_capability(actor, "manage_roles")
    if target.pk == actor.pk:
        return False
    if is_mfa_required(target) or target.role in Role.ADMINS:
        return has_capability(actor, "manage_roles")
    return has_capability(actor, "manage_user_accounts") or has_capability(actor, "manage_roles")


@policy("can_change_roles")
def can_change_roles(actor, target) -> bool:
    return has_capability(actor, "manage_roles") and (target is None or target.pk != actor.pk)


@policy("can_view_own_profile")
def can_view_own_profile(actor, obj=None) -> bool:
    return True

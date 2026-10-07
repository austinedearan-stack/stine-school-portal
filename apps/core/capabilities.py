"""Capability catalog, role ceilings and default groups (ARCHITECTURE.md §5.2).

Single source of truth. ``PortalCapability.Meta.permissions`` must list exactly the codenames in
``CAPABILITIES`` (asserted by tests); ``sync_capability_groups`` creates/updates the default groups
idempotently and is called from a data migration and the ``sync_capabilities`` command.
"""

from __future__ import annotations

from dataclasses import dataclass


class Role:
    STUDENT = "STUDENT"
    STAFF = "STAFF"
    ADMIN = "ADMIN"
    SUPERADMIN = "SUPERADMIN"

    CHOICES = [
        (STUDENT, "Student"),
        (STAFF, "Staff / Lecturer"),
        (ADMIN, "Administrator"),
        (SUPERADMIN, "Super administrator"),
    ]
    ALL = (STUDENT, STAFF, ADMIN, SUPERADMIN)
    ADMINS = (ADMIN, SUPERADMIN)
    STAFF_AND_ADMINS = (STAFF, ADMIN, SUPERADMIN)


CAPABILITY_APP_LABEL = "core"

_S = Role.STAFF
_A = Role.ADMIN
_SA = Role.SUPERADMIN


@dataclass(frozen=True)
class Capability:
    codename: str
    ceiling: frozenset
    groups: tuple

    @property
    def perm(self) -> str:
        return f"{CAPABILITY_APP_LABEL}.{self.codename}"


def _cap(codename, ceiling, groups):
    return Capability(codename, frozenset(ceiling), tuple(groups))


CAPABILITIES: dict[str, Capability] = {
    c.codename: c
    for c in [
        _cap("manage_students", [_A, _SA], ["Registrar"]),
        _cap("manage_staff", [_A, _SA], ["HR", "Registrar"]),
        _cap("manage_academics", [_A, _SA], ["Academic Office"]),
        _cap("manage_units", [_A, _SA], ["Academic Office"]),
        _cap("record_grades", [_S], ["Lecturers"]),
        _cap("manage_grades", [_A, _SA], ["Examinations"]),
        _cap("manage_timetable", [_S, _A, _SA], ["Timetabling"]),
        _cap("manage_hostels", [_A, _SA], ["Accommodation Office"]),
        _cap("manage_request_config", [_A, _SA], ["Student Services"]),
        _cap("review_requests", [_S, _A, _SA], ["Student Services", "Department Reviewers"]),
        _cap("review_all_requests", [_A, _SA], ["Student Services"]),
        _cap("approve_requests", [_S, _A, _SA], ["Heads of Department"]),
        _cap("approve_transfers", [_A, _SA], ["Academic Board"]),
        _cap("execute_transfers", [_A, _SA], ["Registrar"]),
        _cap("manage_clubs", [_A, _SA], ["Student Affairs"]),
        _cap("publish_announcements", [_S, _A, _SA], ["Communications"]),
        _cap("manage_user_accounts", [_A, _SA], ["IT Support"]),
        _cap("view_audit_logs", [_A, _SA], ["Auditor"]),
        _cap(
            "view_statistics",
            [_A, _SA],
            [
                "Registrar",
                "HR",
                "Academic Office",
                "Examinations",
                "Accommodation Office",
                "Student Services",
                "Academic Board",
                "Student Affairs",
                "IT Support",
                "Auditor",
            ],
        ),
        _cap("manage_roles", [_SA], ["Superadmin"]),
        _cap("manage_system_settings", [_SA], ["Superadmin"]),
        _cap("manage_backups", [_SA], ["Superadmin"]),
    ]
}

# Superadmins hold every capability whose ceiling admits them.
SUPERADMIN_GROUP = "Superadmin"


def default_groups() -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for cap in CAPABILITIES.values():
        for group in cap.groups:
            groups.setdefault(group, set()).add(cap.codename)
        if Role.SUPERADMIN in cap.ceiling:
            groups.setdefault(SUPERADMIN_GROUP, set()).add(cap.codename)
    return groups


def role_may_hold(role: str, codename: str) -> bool:
    cap = CAPABILITIES.get(codename)
    return cap is not None and role in cap.ceiling


def sync_capability_groups(group_model, permission_model, content_type_model) -> None:
    """Create the default groups and set their permissions to exactly the catalog's defaults.

    Takes model classes so it works with historical models inside a data migration. Only groups
    named in the catalog are touched; custom groups created by superadmins are left alone.
    """
    content_type, _ = content_type_model.objects.get_or_create(app_label=CAPABILITY_APP_LABEL, model="portalcapability")
    perms = {}
    for cap in CAPABILITIES.values():
        perm, _ = permission_model.objects.get_or_create(
            content_type=content_type, codename=cap.codename, defaults={"name": cap.codename.replace("_", " ")}
        )
        perms[cap.codename] = perm
    for name, codenames in default_groups().items():
        group, _ = group_model.objects.get_or_create(name=name)
        group.permissions.set([perms[c] for c in sorted(codenames)])

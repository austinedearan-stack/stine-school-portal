"""Role/capability-aware navigation.

Each item names a URL and who may see it. Items whose URL is not (yet) registered are skipped, so
the menu only ever links to pages that exist. Visibility here is a convenience only: every view
enforces its own authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse

from apps.core.capabilities import Role


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    section: str
    roles: tuple = Role.ALL
    any_capability: tuple = field(default_factory=tuple)  # visible if the user holds any of these


NAV_ITEMS = [
    NavItem("Dashboard", "core:dashboard", "Home"),
    NavItem("My profile", "accounts:profile", "Home"),
    NavItem("Unit catalogue", "academics:catalog", "Academics"),
    NavItem("My units", "academics:my_units", "Academics", roles=(Role.STUDENT,)),
    NavItem("My teaching", "academics:my_teaching", "Academics", roles=(Role.STAFF,)),
    NavItem("Timetable", "timetable:my_timetable", "Academics"),
    NavItem("Master timetable", "timetable:master", "Academics"),
    NavItem("Manage timetable", "timetable:manage", "Administration", any_capability=("manage_timetable",)),
    NavItem("Hostels", "hostels:catalog", "Services"),
    NavItem("My accommodation", "hostels:my_hostel", "Services", roles=(Role.STUDENT,)),
    NavItem("Clubs & societies", "clubs:directory", "Services"),
    NavItem("My clubs", "clubs:my_clubs", "Services", roles=(Role.STUDENT,)),
    NavItem("My requests", "requests:my_requests", "Services", roles=(Role.STUDENT,)),
    NavItem("Request queue", "requests:queue", "Services", any_capability=(
        "review_requests", "review_all_requests", "approve_requests", "approve_transfers", "execute_transfers")),
    NavItem("Announcements", "notifications:announcements", "Services"),
    NavItem("Notifications", "notifications:list", "Services"),
    NavItem("Publish announcements", "notifications:manage", "Services", any_capability=("publish_announcements",)),
    NavItem("Admin dashboard", "administration:dashboard", "Administration", any_capability=("view_statistics",)),
    NavItem("Users", "administration:users", "Administration",
            any_capability=("manage_user_accounts", "manage_roles", "manage_students", "manage_staff")),
    NavItem("Academic setup", "administration:academics", "Administration",
            any_capability=("manage_academics", "manage_units")),
    NavItem("Hostel management", "hostels:manage", "Administration", any_capability=("manage_hostels",)),
    NavItem("Club management", "clubs:manage", "Administration", any_capability=("manage_clubs",)),
    NavItem("Request settings", "requests:categories", "Administration", any_capability=("manage_request_config",)),
    NavItem("Audit log", "administration:audit_log", "Administration", any_capability=("view_audit_logs",)),
    NavItem("Security events", "administration:security_events", "Administration",
            any_capability=("view_audit_logs",)),
    NavItem("Operations & backups", "administration:operations", "Administration",
            any_capability=("manage_backups",)),
]


def _visible(item: NavItem, user) -> bool:
    if user.role not in item.roles:
        return False
    if item.any_capability:
        from apps.core.authz import has_capability

        return any(has_capability(user, cap) for cap in item.any_capability)
    return True


def navigation_for(user) -> list[tuple[str, list[dict]]]:
    sections: dict[str, list[dict]] = {}
    for item in NAV_ITEMS:
        if not _visible(item, user):
            continue
        try:
            url = reverse(item.url_name)
        except NoReverseMatch:
            continue  # page not available (yet)
        sections.setdefault(item.section, []).append({"label": item.label, "url": url})
    return list(sections.items())

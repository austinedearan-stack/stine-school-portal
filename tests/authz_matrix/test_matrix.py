"""Authorization matrix (ARCHITECTURE.md §5.3) at the page level: who may open which area.

A = allowed (200), D = denied (403), L = redirected to sign-in. The Superadmin group holds every
capability whose ceiling admits superadmins, so superadmins reach every administrative area. Object-level rules (own records,
department scope, offering lecturers) are tested in each feature suite.
"""

import pytest
from django.urls import reverse

from apps.core.capabilities import Role
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db

ACTORS = ["anonymous", "student", "staff", "lecturer", "timetabler", "admin_nocaps", "registrar", "it_support",
          "auditor", "student_services", "superadmin"]

#                                    anon stud staf lect ttbl adm0 regi itsp audi stsv supa
MATRIX = {
    "core:dashboard":                 "L    A    A    A    A    A    A    A    A    A    A",
    "academics:catalog":              "L    A    A    A    A    A    A    A    A    A    A",
    "academics:my_units":             "L    A    D    D    D    D    D    D    D    D    D",
    "academics:my_teaching":          "L    D    A    A    A    D    D    D    D    D    D",
    "timetable:master":               "L    A    A    A    A    A    A    A    A    A    A",
    "timetable:manage":               "L    D    D    D    A    D    D    D    D    D    A",
    "hostels:catalog":                "L    A    A    A    A    A    A    A    A    A    A",
    "hostels:my_hostel":              "L    A    D    D    D    D    D    D    D    D    D",
    "hostels:manage":                 "L    D    D    D    D    D    A    D    D    D    A",
    "clubs:manage":                   "L    D    D    D    D    D    A    D    D    D    A",
    "requests:my_requests":           "L    A    D    D    D    D    D    D    D    D    D",
    "requests:queue":                 "L    D    D    D    D    D    A    D    D    A    A",
    "requests:categories":            "L    D    D    D    D    D    A    D    D    A    A",
    "notifications:manage":           "L    D    D    D    D    D    A    D    D    D    A",
    "administration:dashboard":       "L    D    D    D    D    D    A    A    A    A    A",
    "administration:users":           "L    D    D    D    D    D    A    A    D    D    A",
    "administration:audit_log":       "L    D    D    D    D    D    A    D    A    D    A",
    "administration:security_events": "L    D    D    D    D    D    A    D    A    D    A",
    "administration:academics":       "L    D    D    D    D    D    A    D    D    D    A",
    "administration:student_create":  "L    D    D    D    D    D    A    D    D    D    A",
    "administration:staff_create":    "L    D    D    D    D    D    A    D    D    D    A",
}


def make_actor(kind):
    if kind == "anonymous":
        return None
    if kind == "student":
        return f.student().user
    if kind == "staff":
        return f.staff().user
    if kind == "lecturer":
        return f.staff(groups=["Lecturers"]).user
    if kind == "timetabler":
        return f.staff(groups=["Timetabling"]).user
    groups = {
        "admin_nocaps": [], "it_support": ["IT Support"], "auditor": ["Auditor"],
        "student_services": ["Student Services"], "superadmin": ["Superadmin"],
        # The demo "registrar" holds most office groups, like the seed account.
        "registrar": ["Registrar", "Academic Office", "Student Services", "Accommodation Office", "Student Affairs",
                      "Academic Board", "Communications", "Auditor", "IT Support", "Examinations", "HR"],
    }[kind]
    role = Role.SUPERADMIN if kind == "superadmin" else Role.ADMIN
    user = f.staff(role=role, groups=groups).user if kind == "registrar" else f.user(role, groups=groups)
    enrol(user)
    return user


CASES = [(url, actor, expected) for url, row in MATRIX.items() for actor, expected in zip(ACTORS, row.split(),
                                                                                          strict=True)]


@pytest.mark.parametrize("url_name, actor_kind, expected", CASES, ids=[f"{u}-{a}" for u, a, _ in CASES])
def test_matrix(client, url_name, actor_kind, expected):
    f.semester(is_current=True)
    actor = make_actor(actor_kind)
    if actor is not None:
        login(client, actor)
    response = client.get(reverse(url_name))
    if expected == "A":
        assert response.status_code in (200, 302) and (response.status_code == 200 or
                                                       not response.url.startswith("/accounts/login"))
    elif expected == "D":
        assert response.status_code in (403, 404), f"{actor_kind} reached {url_name}"
    else:
        assert response.status_code == 302 and response.url.startswith("/accounts/login/")


def test_superadmin_only_capabilities_are_not_held_by_admins():
    from apps.core.authz import has_capability

    registrar = make_actor("registrar")
    superadmin = make_actor("superadmin")
    for cap in ("manage_roles", "manage_system_settings", "manage_backups"):
        assert not has_capability(registrar, cap)
        assert has_capability(superadmin, cap)

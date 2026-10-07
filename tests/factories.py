"""Plain-function test data builders (fake data only; @example.test addresses)."""

from __future__ import annotations

import itertools
from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import Group
from django.utils import timezone

from apps.academics.models import (
    AcademicYear,
    Department,
    Faculty,
    OfferingStatus,
    Program,
    Semester,
    Unit,
    UnitOffering,
)
from apps.accounts.models import StaffProfile, StudentProfile, User
from apps.core.capabilities import Role

PASSWORD = "Correct-Horse-Battery-9"  # test fixture only  # secret-scan: allow
_counter = itertools.count(1)


def n() -> int:
    return next(_counter)


def faculty(**kw) -> Faculty:
    i = n()
    return Faculty.objects.create(**{"code": f"F{i}", "name": f"Faculty {i}", **kw})


def department(**kw) -> Department:
    i = n()
    kw.setdefault("faculty", faculty())
    return Department.objects.create(**{"code": f"D{i}", "name": f"Department {i}", **kw})


def program(**kw) -> Program:
    i = n()
    kw.setdefault("department", department())
    return Program.objects.create(**{"code": f"P{i}", "name": f"Program {i}", **kw})


def academic_year(**kw) -> AcademicYear:
    i = n()
    start = date(2000 + i, 9, 1)
    return AcademicYear.objects.create(
        **{"name": f"{2000 + i}/{2001 + i}", "start_date": start, "end_date": start + timedelta(days=360), **kw}
    )


def semester(*, open_registration: bool = True, **kw) -> Semester:
    now = timezone.now()
    kw.setdefault("academic_year", academic_year())
    opens = now - timedelta(days=5) if open_registration else now + timedelta(days=30)
    defaults = {
        "number": 1,
        "name": "Semester 1",
        "start_date": now.date() - timedelta(days=10),
        "end_date": now.date() + timedelta(days=110),
        "registration_opens_at": opens,
        "registration_closes_at": opens + timedelta(days=20),
        "add_drop_deadline": opens + timedelta(days=25),
    }
    defaults.update(kw)
    return Semester.objects.create(**defaults)


def unit(**kw) -> Unit:
    i = n()
    kw.setdefault("department", department())
    return Unit.objects.create(**{"code": f"U{i:04d}", "title": f"Unit {i}", **kw})


def offering(**kw) -> UnitOffering:
    kw.setdefault("unit", unit())
    kw.setdefault("semester", semester())
    kw.setdefault("status", OfferingStatus.OPEN)
    return UnitOffering.objects.create(**kw)


def user(role: str = Role.STUDENT, *, groups=(), **kw) -> User:
    i = n()
    username = kw.pop("username", f"{role.lower()}{i:04d}")
    obj = User.objects.create_user(
        username=username, email=kw.pop("email", f"{username}@example.test"), password=PASSWORD, role=role,
        first_name=kw.pop("first_name", "Test"), last_name=kw.pop("last_name", f"User{i}"), **kw,
    )
    for name in groups:
        obj.groups.add(Group.objects.get(name=name))
    return obj


def student(**kw) -> StudentProfile:
    i = n()
    owner = kw.pop("user", None) or user(Role.STUDENT)
    kw.setdefault("program", program())
    return StudentProfile.objects.create(
        user=owner, **{"student_number": f"S{i:06d}", "admission_date": date(2024, 9, 1), **kw}
    )


def staff(role: str = Role.STAFF, *, groups=(), **kw) -> StaffProfile:
    i = n()
    owner = kw.pop("user", None) or user(role, groups=groups)
    kw.setdefault("department", department())
    return StaffProfile.objects.create(user=owner, **{"staff_number": f"E{i:05d}", **kw})


def at(hour: int, minute: int = 0) -> time:
    return time(hour, minute)


def aware(dt: datetime) -> datetime:
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt

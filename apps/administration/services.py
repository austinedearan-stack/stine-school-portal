"""Administrative services (Phase 10). Every function authorizes, validates, audits with before/after.

Administrators never set another person's password: new accounts get an unusable password and an
emailed one-time reset code, and must choose their own password at first sign-in.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict

from apps.academics.models import AcademicYear, Semester, Unit, UnitPrerequisite
from apps.accounts import passwords
from apps.accounts.models import StaffProfile, StudentProfile, User
from apps.core.audit import diff, record_audit_event
from apps.core.authz import authorize
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.notifications.services import notify

STUDENT_RECORD_FIELDS = ("student_number", "program", "year_of_study", "current_semester_number", "admission_date",
                         "expected_graduation", "academic_status", "disciplinary_status", "campus", "gender")
STAFF_RECORD_FIELDS = ("staff_number", "department", "title", "office", "phone", "is_lecturer")
USER_NAME_FIELDS = ("first_name", "last_name", "email")


class AdminError(ValidationError):
    pass


def _jsonable(values: dict) -> dict:
    return {k: (str(v) if v is not None and not isinstance(v, bool | int | str) else v) for k, v in values.items()}


def _new_user(username, email, first_name, last_name, role) -> User:
    if User.objects.filter(username__iexact=username).exists():
        raise AdminError("That username is already in use.")
    if User.objects.filter(email__iexact=email).exists():
        raise AdminError("That email address is already in use.")
    user = User.objects.create_user(username=username, email=email, password=None, role=role,
                                    first_name=first_name, last_name=last_name)
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])
    return user


@transaction.atomic
def create_student(actor, data: dict, ctx: RequestContext) -> StudentProfile:
    authorize(actor, "can_manage_students", ctx=ctx)
    user = _new_user(data["username"], data["email"], data["first_name"], data["last_name"], Role.STUDENT)
    student = StudentProfile(user=user, **{f: data.get(f) for f in STUDENT_RECORD_FIELDS if f in data})
    student.full_clean()
    student.save()
    record_audit_event(actor, "ACCOUNT.STUDENT_CREATED", student, ctx=ctx,
                       changes={"after": _jsonable({"username": user.username, **{
                           f: getattr(student, f) for f in STUDENT_RECORD_FIELDS}})})
    passwords.request_reset(user, ctx)  # the student sets their own password from the emailed code
    return student


@transaction.atomic
def create_staff(actor, data: dict, role: str, ctx: RequestContext) -> StaffProfile:
    if role == Role.STAFF:
        authorize(actor, "can_manage_staff", ctx=ctx)
    elif role in Role.ADMINS:
        authorize(actor, "can_manage_roles", ctx=ctx)  # admin/superadmin accounts are superadmin business
    else:
        raise AdminError("Choose a staff or administrator role.")
    user = _new_user(data["username"], data["email"], data["first_name"], data["last_name"], role)
    staff = StaffProfile(user=user, **{f: data.get(f) for f in STAFF_RECORD_FIELDS if f in data})
    staff.full_clean()
    staff.save()
    record_audit_event(actor, "ACCOUNT.STAFF_CREATED", staff, ctx=ctx,
                       changes={"after": _jsonable({"username": user.username, "role": role, **{
                           f: getattr(staff, f) for f in STAFF_RECORD_FIELDS}})})
    passwords.request_reset(user, ctx)
    return staff


@transaction.atomic
def update_student_record(actor, student: StudentProfile, data: dict, ctx: RequestContext) -> StudentProfile:
    authorize(actor, "can_manage_students", student, ctx=ctx)
    student = StudentProfile.objects.select_for_update(of=("self",)).select_related("user").get(pk=student.pk)
    user = student.user
    before = {**{f: getattr(student, f) for f in STUDENT_RECORD_FIELDS}, **{f: getattr(user, f) for f in USER_NAME_FIELDS}}
    for field in STUDENT_RECORD_FIELDS:
        if field in data:
            setattr(student, field, data[field])
    for field in USER_NAME_FIELDS:
        if field in data:
            setattr(user, field, data[field])
    user.full_clean(exclude=["password"])
    student.full_clean()
    user.save(update_fields=[*USER_NAME_FIELDS, "updated_at"])
    student.save()
    after = {**{f: getattr(student, f) for f in STUDENT_RECORD_FIELDS}, **{f: getattr(user, f) for f in USER_NAME_FIELDS}}
    changes = diff(_jsonable(before), _jsonable(after))
    if changes["after"]:
        record_audit_event(actor, "STUDENT.RECORD_UPDATED", student, ctx=ctx, changes=changes)
        notify(user, "SYSTEM", "Your student record was updated",
               "The registrar updated your record: " + ", ".join(sorted(changes["after"])) + ".",
               route_name="accounts:profile")
    return student


@transaction.atomic
def update_staff_record(actor, staff: StaffProfile, data: dict, ctx: RequestContext) -> StaffProfile:
    authorize(actor, "can_manage_staff", staff, ctx=ctx)
    staff = StaffProfile.objects.select_for_update(of=("self",)).select_related("user").get(pk=staff.pk)
    if staff.user.role in Role.ADMINS and staff.user_id != actor.pk:
        authorize(actor, "can_manage_roles", staff, ctx=ctx)
    user = staff.user
    before = {**{f: getattr(staff, f) for f in STAFF_RECORD_FIELDS}, **{f: getattr(user, f) for f in USER_NAME_FIELDS}}
    for field in STAFF_RECORD_FIELDS:
        if field in data:
            setattr(staff, field, data[field])
    for field in USER_NAME_FIELDS:
        if field in data:
            setattr(user, field, data[field])
    user.full_clean(exclude=["password"])
    staff.full_clean()
    user.save(update_fields=[*USER_NAME_FIELDS, "updated_at"])
    staff.save()
    after = {**{f: getattr(staff, f) for f in STAFF_RECORD_FIELDS}, **{f: getattr(user, f) for f in USER_NAME_FIELDS}}
    changes = diff(_jsonable(before), _jsonable(after))
    if changes["after"]:
        record_audit_event(actor, "STAFF.RECORD_UPDATED", staff, ctx=ctx, changes=changes)
        if "department" in changes["after"]:
            # Department drives request and announcement scope (ARCHITECTURE.md §5.2): tell the person.
            notify(user, "SECURITY", "Your department changed",
                   f"You are now in {staff.department.name}. Your request and announcement scope changed with it.")
    return staff


# --- Academic setup -------------------------------------------------------------------------------


@transaction.atomic
def save_setup_object(actor, obj, capability_policy: str, ctx: RequestContext, *, changed_fields=()):
    authorize(actor, capability_policy, None if obj._state.adding else obj, ctx=ctx)
    creating = obj._state.adding
    before = {} if creating else _jsonable(model_to_dict(type(obj).objects.get(pk=obj.pk)))
    obj.full_clean()
    obj.save()
    after = _jsonable(model_to_dict(obj))
    record_audit_event(actor, f"SETUP.{type(obj).__name__.upper()}_{'CREATED' if creating else 'UPDATED'}", obj,
                       ctx=ctx, changes={"after": after} if creating else diff(before, after))
    return obj


@transaction.atomic
def set_current_semester(actor, semester: Semester, ctx: RequestContext) -> Semester:
    authorize(actor, "can_manage_academics", semester, ctx=ctx)
    Semester.objects.filter(is_current=True).exclude(pk=semester.pk).update(is_current=False)
    Semester.objects.filter(pk=semester.pk).update(is_current=True)
    AcademicYear.objects.filter(is_current=True).exclude(pk=semester.academic_year_id).update(is_current=False)
    AcademicYear.objects.filter(pk=semester.academic_year_id).update(is_current=True)
    record_audit_event(actor, "SETUP.CURRENT_SEMESTER_SET", semester, ctx=ctx)
    semester.refresh_from_db()
    return semester


def _reachable(start: Unit, target: Unit) -> bool:
    """Is ``target`` a (transitive) prerequisite of ``start``? Used to refuse cycles."""
    seen, frontier = set(), [start.pk]
    while frontier:
        current = frontier.pop()
        if current == target.pk:
            return True
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(UnitPrerequisite.objects.filter(unit_id=current).values_list("prerequisite_id", flat=True))
    return False


@transaction.atomic
def add_prerequisite(actor, unit: Unit, prerequisite: Unit, ctx: RequestContext) -> UnitPrerequisite:
    authorize(actor, "can_manage_units", unit, ctx=ctx)
    if unit.pk == prerequisite.pk or _reachable(prerequisite, unit):
        raise AdminError(f"{prerequisite.code} already depends on {unit.code}; that would create a cycle.")
    link, created = UnitPrerequisite.objects.get_or_create(unit=unit, prerequisite=prerequisite)
    if created:
        record_audit_event(actor, "SETUP.PREREQUISITE_ADDED", unit, ctx=ctx,
                           changes={"after": {"prerequisite": prerequisite.code}})
    return link


@transaction.atomic
def remove_prerequisite(actor, link: UnitPrerequisite, ctx: RequestContext) -> None:
    authorize(actor, "can_manage_units", link.unit, ctx=ctx)
    record_audit_event(actor, "SETUP.PREREQUISITE_REMOVED", link.unit, ctx=ctx,
                       changes={"before": {"prerequisite": link.prerequisite.code}})
    link.delete()

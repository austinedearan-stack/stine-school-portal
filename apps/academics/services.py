"""Unit registration, drop and grading (ARCHITECTURE.md §5.3, DATABASE.md §3).

Every rule is re-checked inside one transaction holding the locks in the documented order
(StudentProfile row, then UnitOffering row), and the partial unique index on live registrations is
the database backstop, so concurrent requests cannot exceed capacity or the credit limit, or create
duplicate registrations.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.academics.models import (
    GRADE_POINTS,
    Grade,
    OfferingStatus,
    RegistrationStatus,
    UnitOffering,
    UnitRegistration,
)
from apps.accounts.models import AcademicStatus, DisciplinaryStatus, StudentProfile
from apps.core.audit import diff, record_audit_event
from apps.core.authz import authorize, has_capability
from apps.core.context import RequestContext
from apps.notifications.services import notify

REGISTRABLE_ACADEMIC_STATUSES = (AcademicStatus.ACTIVE, AcademicStatus.PROBATION)
PASSING_GRADES = (Grade.A, Grade.B, Grade.C, Grade.D)


class RegistrationError(ValidationError):
    """A business rule refused the registration change (shown to the user, never a 500)."""


def _registered_credits(student, semester) -> int:
    return (
        UnitRegistration.objects.filter(student=student, semester=semester, status=RegistrationStatus.REGISTERED)
        .aggregate(total=Sum("unit__credit_hours"))["total"] or 0
    )


def _missing_prerequisites(student, unit) -> list[str]:
    required = list(unit.prerequisite_links.select_related("prerequisite"))
    passed = set(
        UnitRegistration.objects.filter(
            student=student, unit__in=[link.prerequisite for link in required],
            status=RegistrationStatus.COMPLETED, grade__in=PASSING_GRADES,
        ).values_list("unit_id", flat=True)
    )
    return [link.prerequisite.code for link in required if link.prerequisite_id not in passed]


def _clashes(student, offering) -> list[str]:
    from apps.timetable.models import TimetableEntry

    mine = TimetableEntry.objects.filter(
        semester=offering.semester, offering__registrations__student=student,
        offering__registrations__status=RegistrationStatus.REGISTERED,
    ).select_related("offering__unit")
    clashes = []
    for new in offering.timetable_entries.all():
        for existing in mine:
            if (existing.day_of_week == new.day_of_week and existing.start_time < new.end_time
                    and new.start_time < existing.end_time):
                clashes.append(existing.offering.unit.code)
    return sorted(set(clashes))


def _check_window(offering, *, override: bool, for_drop: bool) -> None:
    now = timezone.now()
    semester = offering.semester
    if override:
        return
    if for_drop:
        if now > semester.add_drop_deadline:
            raise RegistrationError("The add/drop deadline for this semester has passed.")
        return
    if not (semester.registration_opens_at <= now <= semester.registration_closes_at):
        raise RegistrationError("Unit registration is not open for this semester.")


def eligibility_problems(student: StudentProfile, offering: UnitOffering) -> list[str]:
    """Every rule except the window and locks; used for display and re-checked under lock on submit."""
    problems = []
    if student.academic_status not in REGISTRABLE_ACADEMIC_STATUSES:
        problems.append(f"Your academic status ({student.get_academic_status_display()}) does not allow registration.")
    if student.disciplinary_status == DisciplinaryStatus.SANCTIONED:
        problems.append("A disciplinary sanction currently blocks registration.")
    if offering.status != OfferingStatus.OPEN or not offering.unit.is_active:
        problems.append("This offering is not open for registration.")
    eligible = list(offering.eligible_programs.all())
    if eligible and student.program not in eligible:
        problems.append("This offering is restricted to other programs.")
    if student.year_of_study < offering.min_year:
        problems.append(f"This offering requires year {offering.min_year} or above.")
    missing = _missing_prerequisites(student, offering.unit)
    if missing:
        problems.append("Missing prerequisites: " + ", ".join(missing) + ".")
    return problems


def register(actor, student: StudentProfile, offering: UnitOffering, ctx: RequestContext, *,
             override_reason: str = "") -> UnitRegistration:
    override = actor.pk != student.user_id
    if override:
        authorize(actor, "can_override_registration", student, ctx=ctx)
        if not override_reason.strip():
            raise RegistrationError("A reason is required for a registration override.")
    else:
        authorize(actor, "can_register_units", student, ctx=ctx)

    with transaction.atomic():
        student = StudentProfile.objects.select_for_update(of=("self",)).select_related("program").get(pk=student.pk)
        offering = UnitOffering.objects.select_for_update(of=("self",)).select_related("unit", "semester").get(pk=offering.pk)
        _check_window(offering, override=override, for_drop=False)
        problems = eligibility_problems(student, offering)
        if problems:
            raise RegistrationError(problems[0])
        if UnitRegistration.objects.filter(student=student, unit=offering.unit, semester=offering.semester,
                                           status__in=(RegistrationStatus.REGISTERED,
                                                       RegistrationStatus.COMPLETED)).exists():
            raise RegistrationError("You are already registered for this unit this semester.")
        taken = UnitRegistration.objects.filter(offering=offering, status=RegistrationStatus.REGISTERED).count()
        if taken >= offering.capacity:
            raise RegistrationError("This offering is full.")
        credits = _registered_credits(student, offering.semester) + offering.unit.credit_hours
        if credits > student.program.max_credits_per_semester:
            raise RegistrationError(
                f"Registering would bring you to {credits} credit hours; the limit is "
                f"{student.program.max_credits_per_semester}.")
        clashes = _clashes(student, offering)
        if clashes:
            raise RegistrationError("This offering clashes with your timetable: " + ", ".join(clashes) + ".")
        try:
            with transaction.atomic():
                registration = UnitRegistration.objects.create(
                    student=student, offering=offering, unit=offering.unit, semester=offering.semester,
                    registered_by=actor if override else None,
                )
        except IntegrityError as exc:  # concurrent duplicate: the partial unique index is the backstop
            raise RegistrationError("You are already registered for this unit this semester.") from exc
        record_audit_event(actor, "REGISTRATION.OVERRIDE_REGISTER" if override else "REGISTRATION.REGISTER",
                           registration, ctx=ctx, changes={"after": {
                               "offering": str(offering.pk), "unit": offering.unit.code, "credits": credits,
                               **({"reason": override_reason.strip()} if override else {})}})
        notify(student.user, "REGISTRATION", f"Registered for {offering.unit.code}",
               f"{offering.unit.title} ({offering.unit.credit_hours} credit hours)" +
               (" — registered by the registrar." if override else "."),
               route_name="academics:my_units")
    return registration


def drop(actor, registration: UnitRegistration, ctx: RequestContext, *, override_reason: str = "") -> UnitRegistration:
    student = registration.student
    override = actor.pk != student.user_id
    if override:
        authorize(actor, "can_override_registration", student, ctx=ctx)
        if not override_reason.strip():
            raise RegistrationError("A reason is required for a registration override.")
    else:
        authorize(actor, "can_register_units", student, ctx=ctx)

    with transaction.atomic():
        student = StudentProfile.objects.select_for_update(of=("self",)).select_related("program").get(pk=student.pk)
        registration = UnitRegistration.objects.select_for_update(of=("self",)).select_related(
            "offering__semester", "unit").get(pk=registration.pk)
        if registration.student_id != student.pk:
            raise RegistrationError("Registration not found.")
        if registration.status != RegistrationStatus.REGISTERED:
            raise RegistrationError("Only current registrations can be dropped.")
        _check_window(registration.offering, override=override, for_drop=True)
        remaining = _registered_credits(student, registration.semester) - registration.unit.credit_hours
        minimum = student.program.min_credits_per_semester
        if 0 < remaining < minimum:
            raise RegistrationError(f"Dropping would leave {remaining} credit hours; the minimum is {minimum}.")
        registration.status = RegistrationStatus.DROPPED
        registration.dropped_at = timezone.now()
        registration.save(update_fields=["status", "dropped_at", "updated_at"])
        record_audit_event(actor, "REGISTRATION.OVERRIDE_DROP" if override else "REGISTRATION.DROP", registration,
                           ctx=ctx, changes={"before": {"status": "REGISTERED"}, "after": {
                               "status": "DROPPED", **({"reason": override_reason.strip()} if override else {})}})
        notify(student.user, "REGISTRATION", f"Dropped {registration.unit.code}",
               "The unit was removed from your registration.", route_name="academics:my_units")
    return registration


def record_grade(actor, registration: UnitRegistration, grade: str, ctx: RequestContext) -> UnitRegistration:
    """Lecturers (record_grades) grade their own offerings once; amending a grade needs manage_grades."""
    if grade not in Grade.values:
        raise RegistrationError("Unknown grade.")
    with transaction.atomic():
        registration = UnitRegistration.objects.select_for_update(of=("self",)).select_related("offering__lecturer").get(
            pk=registration.pk)
        authorize(actor, "can_record_grade", registration, ctx=ctx)
        if registration.status not in (RegistrationStatus.REGISTERED, RegistrationStatus.COMPLETED,
                                       RegistrationStatus.FAILED):
            raise RegistrationError("Only registered students can be graded.")
        amending = registration.grade not in ("", Grade.I)
        if amending and not has_capability(actor, "manage_grades"):
            raise RegistrationError("This grade is already recorded; amendments go through the examinations office.")
        before = {"grade": registration.grade, "status": registration.status}
        registration.grade = grade
        registration.grade_points = GRADE_POINTS.get(grade)
        registration.status = (RegistrationStatus.COMPLETED if grade in PASSING_GRADES else
                               RegistrationStatus.FAILED if grade == Grade.E else RegistrationStatus.REGISTERED)
        registration.graded_by = actor
        registration.graded_at = timezone.now()
        registration.save(update_fields=["grade", "grade_points", "status", "graded_by", "graded_at", "updated_at"])
        record_audit_event(actor, "GRADE.AMENDED" if amending else "GRADE.RECORDED", registration, ctx=ctx,
                           changes=diff(before, {"grade": registration.grade, "status": registration.status}))
        notify(registration.student.user, "REGISTRATION", f"Result available for {registration.unit.code}",
               "A result was recorded for one of your units.", route_name="academics:my_units")
    return registration

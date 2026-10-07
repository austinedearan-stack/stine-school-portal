"""Timetable changes (ARCHITECTURE.md §5.3, DATABASE.md §2.3 and §3).

Clash protection has two layers: the checks below (readable messages, every engine) and the
PostgreSQL exclusion constraints (concurrent clashing inserts impossible). Locks follow the documented
order: Venue row, then the lecturer's StaffProfile row, then the offering row.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.academics.models import OfferingStatus, RegistrationStatus, UnitOffering
from apps.accounts.models import StaffProfile
from apps.core.audit import diff, record_audit_event
from apps.core.authz import authorize
from apps.core.context import RequestContext
from apps.notifications.services import notify_many
from apps.timetable.models import TimetableEntry, Venue

ENTRY_FIELDS = ("offering", "venue", "day_of_week", "start_time", "end_time", "class_type", "student_group")


class TimetableConflict(ValidationError):
    pass


def _overlapping(semester, day, start, end, exclude_pk=None):
    qs = TimetableEntry.objects.filter(semester=semester, day_of_week=day, start_time__lt=end, end_time__gt=start)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.select_related("offering__unit", "venue")


def conflicts(*, offering, venue, day, start, end, student_group=None, lecturer=None, exclude_pk=None) -> list[str]:
    """Readable clash descriptions for a proposed slot (empty list = no clash)."""
    problems = []
    if start >= end:
        return ["The end time must be after the start time."]
    overlapping = _overlapping(offering.semester, day, start, end, exclude_pk)
    for other in overlapping.filter(venue=venue):
        problems.append(f"{venue.code} is already booked for {other.offering.unit.code} "
                        f"({other.start_time:%H:%M}–{other.end_time:%H:%M}).")
    lecturer = lecturer if lecturer is not None else offering.lecturer
    if lecturer is not None:
        for other in overlapping.filter(lecturer=lecturer):
            problems.append(f"{lecturer.user.full_name} already teaches {other.offering.unit.code} "
                            f"({other.start_time:%H:%M}–{other.end_time:%H:%M}).")
    if student_group is not None:
        for other in overlapping.filter(student_group=student_group):
            problems.append(f"Group {student_group} already has {other.offering.unit.code} "
                            f"({other.start_time:%H:%M}–{other.end_time:%H:%M}).")
    return problems


def _validate(data, exclude_pk=None) -> None:
    offering, venue = data["offering"], data["venue"]
    if offering.status == OfferingStatus.CANCELLED:
        raise TimetableConflict("This offering is cancelled.")
    if not venue.is_active:
        raise TimetableConflict("This venue is not in use.")
    enrolled = offering.registrations.filter(status=RegistrationStatus.REGISTERED).count()
    if enrolled > venue.capacity:
        raise TimetableConflict(f"{venue.code} seats {venue.capacity}, but {enrolled} students are registered.")
    group = data.get("student_group")
    eligible = set(offering.eligible_programs.values_list("pk", flat=True))
    if group is not None and eligible and group.program_id not in eligible:
        raise TimetableConflict("That student group's program is not eligible for this offering.")
    problems = conflicts(offering=offering, venue=venue, day=data["day_of_week"], start=data["start_time"],
                         end=data["end_time"], student_group=group, exclude_pk=exclude_pk)
    if problems:
        raise TimetableConflict(problems)


def _lock(venue, offering) -> UnitOffering:
    Venue.objects.select_for_update().filter(pk=venue.pk).first()
    if offering.lecturer_id:
        StaffProfile.objects.select_for_update().filter(pk=offering.lecturer_id).first()
    return UnitOffering.objects.select_for_update(of=("self",)).select_related("unit", "semester", "lecturer__user").get(
        pk=offering.pk)


def _save(entry: TimetableEntry) -> None:
    try:
        with transaction.atomic():
            entry.save()
    except IntegrityError as exc:  # exclusion constraint (concurrent clash) or check constraint
        raise TimetableConflict("This slot clashes with another booking. Refresh and try again.") from exc


def _notify_registered(offering, title, body) -> None:
    users = [r.student.user for r in offering.registrations.filter(status=RegistrationStatus.REGISTERED)
             .select_related("student__user")]
    if offering.lecturer is not None:
        users.append(offering.lecturer.user)
    notify_many(users, "TIMETABLE", title, body, route_name="timetable:my_timetable")


def _snapshot(entry) -> dict:
    return {
        "venue": entry.venue.code, "day": entry.day_of_week, "start": entry.start_time.strftime("%H:%M"),
        "end": entry.end_time.strftime("%H:%M"), "type": entry.class_type,
        "group": str(entry.student_group) if entry.student_group else "",
    }


@transaction.atomic
def create_entry(actor, data: dict, ctx: RequestContext) -> TimetableEntry:
    authorize(actor, "can_manage_timetable", ctx=ctx)
    offering = _lock(data["venue"], data["offering"])
    data = {**data, "offering": offering}
    _validate(data)
    entry = TimetableEntry(**{k: data.get(k) for k in ENTRY_FIELDS}, semester=offering.semester,
                           lecturer=offering.lecturer)
    _save(entry)
    record_audit_event(actor, "TIMETABLE.CREATED", entry, ctx=ctx, changes={"after": _snapshot(entry)})
    _notify_registered(offering, f"{offering.unit.code} timetable updated",
                       f"New {entry.get_class_type_display().lower()}: {entry.get_day_of_week_display()} "
                       f"{entry.start_time:%H:%M}–{entry.end_time:%H:%M}, {entry.venue.name}.")
    return entry


@transaction.atomic
def update_entry(actor, entry: TimetableEntry, data: dict, ctx: RequestContext) -> TimetableEntry:
    authorize(actor, "can_manage_timetable", entry, ctx=ctx)
    offering = _lock(data["venue"], data["offering"])
    entry = TimetableEntry.objects.select_for_update(of=("self",)).select_related("venue", "student_group").get(
        pk=entry.pk)
    before = _snapshot(entry)
    data = {**data, "offering": offering}
    _validate(data, exclude_pk=entry.pk)
    for field in ENTRY_FIELDS:
        setattr(entry, field, data.get(field))
    entry.semester, entry.lecturer = offering.semester, offering.lecturer
    _save(entry)
    record_audit_event(actor, "TIMETABLE.UPDATED", entry, ctx=ctx, changes=diff(before, _snapshot(entry)))
    _notify_registered(offering, f"{offering.unit.code} timetable changed",
                       f"Now {entry.get_day_of_week_display()} {entry.start_time:%H:%M}–{entry.end_time:%H:%M}, "
                       f"{entry.venue.name}.")
    return entry


@transaction.atomic
def delete_entry(actor, entry: TimetableEntry, ctx: RequestContext) -> None:
    authorize(actor, "can_manage_timetable", entry, ctx=ctx)
    entry = TimetableEntry.objects.select_for_update(of=("self",)).select_related("venue", "offering__unit").get(
        pk=entry.pk)
    snapshot = _snapshot(entry)
    offering = entry.offering
    entry.delete()
    record_audit_event(actor, "TIMETABLE.DELETED", object_type="TimetableEntry", object_id=str(entry.pk), ctx=ctx,
                       changes={"before": snapshot})
    _notify_registered(offering, f"{offering.unit.code} class cancelled",
                       f"The {snapshot['type'].lower()} on day {snapshot['day']} at {snapshot['start']} was removed.")


@transaction.atomic
def set_offering_lecturer(actor, offering: UnitOffering, lecturer: StaffProfile | None, ctx: RequestContext):
    """Change an offering's lecturer and its timetable entries together, re-checking lecturer clashes."""
    authorize(actor, "can_manage_units", offering, ctx=ctx)
    if lecturer is not None:
        StaffProfile.objects.select_for_update().filter(pk=lecturer.pk).first()
    offering = UnitOffering.objects.select_for_update(of=("self",)).select_related("unit", "lecturer__user").get(
        pk=offering.pk)
    before = {"lecturer": offering.lecturer.user.username if offering.lecturer else ""}
    if lecturer is not None:
        for entry in offering.timetable_entries.all():
            clash = [p for p in conflicts(offering=offering, venue=entry.venue, day=entry.day_of_week,
                                          start=entry.start_time, end=entry.end_time, lecturer=lecturer,
                                          exclude_pk=entry.pk) if "already teaches" in p]
            if clash:
                raise TimetableConflict(clash)
    offering.lecturer = lecturer
    offering.save(update_fields=["lecturer", "updated_at"])
    try:
        with transaction.atomic():
            offering.timetable_entries.update(lecturer=lecturer)
    except IntegrityError as exc:
        raise TimetableConflict("The new lecturer has a clashing class.") from exc
    record_audit_event(actor, "OFFERING.LECTURER_CHANGED", offering, ctx=ctx,
                       changes=diff(before, {"lecturer": lecturer.user.username if lecturer else ""}))
    return offering


@transaction.atomic
def save_venue(actor, venue: Venue, ctx: RequestContext) -> Venue:
    authorize(actor, "can_manage_timetable", venue, ctx=ctx)
    created = venue._state.adding
    venue.full_clean()
    venue.save()
    record_audit_event(actor, "VENUE.CREATED" if created else "VENUE.UPDATED", venue, ctx=ctx,
                       changes={"after": {"code": venue.code, "capacity": venue.capacity, "active": venue.is_active}})
    return venue

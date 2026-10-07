"""Hostel booking, applications and allocations (ARCHITECTURE.md D12, DATABASE.md §2.4 and §3).

Locks are taken in the documented order - StudentProfile row, then Bed row(s) (by id), then the
allocation/application rows - and expired offers are marked EXPIRED inside the lock before any
availability check. The partial unique indexes (one holder per bed per semester, one bed per student
per semester) are the database backstop for every race.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Gender, StudentProfile
from apps.core.audit import record_audit_event
from apps.core.authz import authorize
from apps.core.context import RequestContext
from apps.hostels.models import (
    HOLDING_ALLOCATION_STATUSES,
    AllocationStatus,
    ApplicationStatus,
    Bed,
    BookingMode,
    GenderPolicy,
    HostelAllocation,
    HostelApplication,
    HostelBookingWindow,
    HostelPreference,
    SpaceStatus,
)
from apps.notifications.services import notify


class HostelError(ValidationError):
    pass


# --- Helpers --------------------------------------------------------------------------------------


def open_window(semester, mode: str | None = None) -> HostelBookingWindow | None:
    if semester is None:
        return None
    now = timezone.now()
    qs = HostelBookingWindow.objects.filter(semester=semester, opens_at__lte=now, closes_at__gte=now)
    if mode:
        qs = qs.filter(mode=mode)
    return qs.order_by("-opens_at").first()


def gender_allowed(student: StudentProfile, hostel) -> bool:
    if hostel.gender_policy == GenderPolicy.MIXED:
        return True
    return student.gender in (Gender.FEMALE, Gender.MALE) and student.gender == hostel.gender_policy


def expire_stale_offers(*, semester=None, bed=None, student=None) -> int:
    """Mark offers past their acceptance deadline EXPIRED (their applications go back to APPROVED)."""
    qs = HostelAllocation.objects.filter(status=AllocationStatus.PENDING_ACCEPTANCE,
                                         offer_expires_at__lt=timezone.now())
    if semester is not None:
        qs = qs.filter(semester=semester)
    if bed is not None:
        qs = qs.filter(bed=bed)
    if student is not None:
        qs = qs.filter(student=student)
    expired = 0
    for allocation in qs.select_for_update(of=("self",)).select_related("application", "student__user"):
        allocation.status = AllocationStatus.EXPIRED
        allocation.ended_at = timezone.now()
        allocation.save(update_fields=["status", "ended_at", "updated_at"])
        if allocation.application_id:
            HostelApplication.objects.filter(pk=allocation.application_id,
                                             status=ApplicationStatus.ALLOCATED).update(status=ApplicationStatus.APPROVED)
        notify(allocation.student.user, "HOSTEL", "Your hostel offer expired",
               "The offer was not accepted in time and has been withdrawn.", route_name="hostels:my_hostel")
        expired += 1
    return expired


def _bed_is_free(bed: Bed, semester) -> bool:
    room, building, hostel = bed.room, bed.room.floor.building, bed.room.floor.building.hostel
    if bed.status != SpaceStatus.AVAILABLE or room.status != SpaceStatus.AVAILABLE:
        return False
    if not building.is_active or not hostel.is_active:
        return False
    return not HostelAllocation.objects.filter(bed=bed, semester=semester,
                                               status__in=HOLDING_ALLOCATION_STATUSES).exists()


def _lock_student(student) -> StudentProfile:
    return StudentProfile.objects.select_for_update(of=("self",)).select_related("user").get(pk=student.pk)


def _lock_bed(bed) -> Bed:
    return Bed.objects.select_for_update(of=("self",)).select_related("room__floor__building__hostel").get(pk=bed.pk)


def _create_allocation(**fields) -> HostelAllocation:
    try:
        with transaction.atomic():
            return HostelAllocation.objects.create(**fields)
    except IntegrityError as exc:  # partial unique index: bed or student already holding
        raise HostelError("That bed was just taken, or you already hold a bed this semester.") from exc


def _assert_no_holding(student, semester) -> None:
    if HostelAllocation.objects.filter(student=student, semester=semester,
                                       status__in=HOLDING_ALLOCATION_STATUSES).exists():
        raise HostelError("You already hold a bed (or an offer) for this semester.")


# --- Student actions ------------------------------------------------------------------------------


def book_bed(actor, student: StudentProfile, bed: Bed, semester, ctx: RequestContext) -> HostelAllocation:
    """Direct booking: the student takes a free bed during a DIRECT_BOOKING window."""
    authorize(actor, "can_book_bed", student, ctx=ctx)
    if open_window(semester, BookingMode.DIRECT_BOOKING) is None:
        raise HostelError("Direct hostel booking is not open.")
    with transaction.atomic():
        student = _lock_student(student)
        bed = _lock_bed(bed)
        expire_stale_offers(semester=semester, bed=bed)
        expire_stale_offers(semester=semester, student=student)
        hostel = bed.room.floor.building.hostel
        if not gender_allowed(student, hostel):
            raise HostelError(f"{hostel.name} is a {hostel.get_gender_policy_display().lower()} hostel.")
        _assert_no_holding(student, semester)
        if not _bed_is_free(bed, semester):
            raise HostelError("That bed is no longer available.")
        allocation = _create_allocation(student=student, bed=bed, semester=semester, status=AllocationStatus.ACTIVE)
        record_audit_event(actor, "HOSTEL.BOOKED", allocation, ctx=ctx, changes={"after": {"bed": str(bed)}})
        notify(student.user, "HOSTEL", "Bed booked", f"You booked {bed}.", route_name="hostels:my_hostel")
    return allocation


def apply(actor, student: StudentProfile, semester, preferences: list, room_type: str, special_needs: str,
          ctx: RequestContext) -> HostelApplication:
    authorize(actor, "can_book_bed", student, ctx=ctx)
    if open_window(semester, BookingMode.APPLICATION) is None:
        raise HostelError("Hostel applications are not open.")
    preferences = [h for h in preferences if h is not None]
    if not 1 <= len(preferences) <= 3 or len({h.pk for h in preferences}) != len(preferences):
        raise HostelError("Choose between one and three different hostels.")
    for hostel in preferences:
        if not hostel.is_active or not gender_allowed(student, hostel):
            raise HostelError(f"You cannot apply to {hostel.name}.")
    with transaction.atomic():
        student = _lock_student(student)
        _assert_no_holding(student, semester)
        try:
            with transaction.atomic():
                application = HostelApplication.objects.create(
                    student=student, semester=semester, preferred_room_type=room_type or "",
                    special_needs=(special_needs or "").strip())
        except IntegrityError as exc:
            raise HostelError("You already have an open application for this semester.") from exc
        HostelPreference.objects.bulk_create(
            HostelPreference(application=application, hostel=h, rank=i) for i, h in enumerate(preferences, start=1))
        record_audit_event(actor, "HOSTEL.APPLIED", application, ctx=ctx,
                           changes={"after": {"preferences": [h.code for h in preferences]}})
    return application


def respond_to_offer(actor, allocation: HostelAllocation, accept: bool, ctx: RequestContext) -> HostelAllocation:
    authorize(actor, "can_book_bed", allocation.student, ctx=ctx)
    with transaction.atomic():
        _lock_student(allocation.student)
        _lock_bed(allocation.bed)
        expire_stale_offers(bed=allocation.bed)  # committed even though the response below is refused
        allocation = HostelAllocation.objects.select_for_update(of=("self",)).get(pk=allocation.pk)
        still_open = allocation.status == AllocationStatus.PENDING_ACCEPTANCE
        if still_open:
            _apply_response(actor, allocation, accept, ctx)
    if not still_open:
        raise HostelError("This offer is no longer open.")
    return allocation


def _apply_response(actor, allocation: HostelAllocation, accept: bool, ctx: RequestContext) -> None:
    allocation.status = AllocationStatus.ACTIVE if accept else AllocationStatus.DECLINED
    if not accept:
        allocation.ended_at = timezone.now()
        if allocation.application_id:
            HostelApplication.objects.filter(pk=allocation.application_id).update(status=ApplicationStatus.APPROVED)
    allocation.save(update_fields=["status", "ended_at", "updated_at"])
    record_audit_event(actor, "HOSTEL.OFFER_ACCEPTED" if accept else "HOSTEL.OFFER_DECLINED", allocation, ctx=ctx)


def cancel(actor, student: StudentProfile, semester, ctx: RequestContext) -> None:
    """Student gives up their bed/offer and closes their application for the semester."""
    authorize(actor, "can_book_bed", student, ctx=ctx)
    with transaction.atomic():
        student = _lock_student(student)
        allocation = HostelAllocation.objects.filter(student=student, semester=semester,
                                                     status__in=HOLDING_ALLOCATION_STATUSES).first()
        application = HostelApplication.objects.select_for_update().filter(
            student=student, semester=semester).exclude(
            status__in=[ApplicationStatus.CANCELLED, ApplicationStatus.REJECTED]).first()
        if allocation is None and application is None:
            raise HostelError("You have nothing to cancel for this semester.")
        if allocation is not None:
            _lock_bed(allocation.bed)
            allocation = HostelAllocation.objects.select_for_update(of=("self",)).get(pk=allocation.pk)
            allocation.status = AllocationStatus.CANCELLED
            allocation.ended_at = timezone.now()
            allocation.save(update_fields=["status", "ended_at", "updated_at"])
            record_audit_event(actor, "HOSTEL.CANCELLED", allocation, ctx=ctx)
        if application is not None:
            application.status = ApplicationStatus.CANCELLED
            application.save(update_fields=["status", "updated_at"])
            record_audit_event(actor, "HOSTEL.APPLICATION_CANCELLED", application, ctx=ctx)


# --- Accommodation office ------------------------------------------------------------------------


def decide_application(actor, application: HostelApplication, status: str, note: str,
                       ctx: RequestContext) -> HostelApplication:
    authorize(actor, "can_manage_hostels", application, ctx=ctx)
    if status not in (ApplicationStatus.UNDER_REVIEW, ApplicationStatus.APPROVED, ApplicationStatus.REJECTED,
                      ApplicationStatus.WAITLISTED):
        raise HostelError("Unsupported decision.")
    with transaction.atomic():
        application = HostelApplication.objects.select_for_update(of=("self",)).select_related(
            "student__user").get(pk=application.pk)
        if application.status in (ApplicationStatus.CANCELLED, ApplicationStatus.ALLOCATED):
            raise HostelError("This application can no longer be changed.")
        before = application.status
        application.status, application.decision_note = status, (note or "").strip()
        application.decided_by, application.decided_at = actor, timezone.now()
        application.save(update_fields=["status", "decision_note", "decided_by", "decided_at", "updated_at"])
        record_audit_event(actor, "HOSTEL.APPLICATION_DECIDED", application, ctx=ctx,
                           changes={"before": {"status": before}, "after": {"status": status}})
        notify(application.student.user, "HOSTEL", "Hostel application update",
               f"Your application is now: {application.get_status_display()}.", route_name="hostels:my_hostel")
    return application


def offer_bed(actor, student: StudentProfile, bed: Bed, semester, ctx: RequestContext, *,
              application: HostelApplication | None = None) -> HostelAllocation:
    authorize(actor, "can_manage_hostels", student, ctx=ctx)
    window = HostelBookingWindow.objects.filter(semester=semester).order_by("-opens_at").first()
    hours = window.acceptance_hours if window else 48
    with transaction.atomic():
        student = _lock_student(student)
        bed = _lock_bed(bed)
        expire_stale_offers(semester=semester, bed=bed)
        expire_stale_offers(semester=semester, student=student)
        hostel = bed.room.floor.building.hostel
        if not gender_allowed(student, hostel):
            raise HostelError(f"{hostel.name}'s gender policy does not match this student.")
        _assert_no_holding(student, semester)
        if not _bed_is_free(bed, semester):
            raise HostelError("That bed is not available.")
        if application is not None:
            application = HostelApplication.objects.select_for_update(of=("self",)).get(pk=application.pk)
            if application.student_id != student.pk or application.status not in (
                    ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW, ApplicationStatus.APPROVED,
                    ApplicationStatus.WAITLISTED):
                raise HostelError("That application cannot receive an offer.")
        allocation = _create_allocation(
            student=student, bed=bed, semester=semester, application=application, allocated_by=actor,
            status=AllocationStatus.PENDING_ACCEPTANCE, offer_expires_at=timezone.now() + timedelta(hours=hours))
        if application is not None:
            application.status, application.decided_by, application.decided_at = (
                ApplicationStatus.ALLOCATED, actor, timezone.now())
            application.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        record_audit_event(actor, "HOSTEL.OFFERED", allocation, ctx=ctx,
                           changes={"after": {"bed": str(bed), "expires": allocation.offer_expires_at.isoformat()}})
        notify(student.user, "HOSTEL", "You have a hostel offer",
               f"You were offered {bed}. Accept within {hours} hours.", route_name="hostels:my_hostel")
    return allocation


def transfer(actor, allocation: HostelAllocation, new_bed: Bed, ctx: RequestContext) -> HostelAllocation:
    authorize(actor, "can_manage_hostels", allocation, ctx=ctx)
    with transaction.atomic():
        _lock_student(allocation.student)
        first, second = sorted([allocation.bed_id, new_bed.pk], key=str)  # both beds, in UUID order
        Bed.objects.select_for_update().filter(pk=first).first()
        Bed.objects.select_for_update().filter(pk=second).first()
        new_bed = Bed.objects.select_related("room__floor__building__hostel").get(pk=new_bed.pk)
        expire_stale_offers(semester=allocation.semester, bed=new_bed)
        allocation = HostelAllocation.objects.select_for_update(of=("self",)).select_related(
            "student__user", "bed").get(pk=allocation.pk)
        if allocation.status != AllocationStatus.ACTIVE:
            raise HostelError("Only active allocations can be transferred.")
        if new_bed.pk == allocation.bed_id or not _bed_is_free(new_bed, allocation.semester):
            raise HostelError("The new bed is not available.")
        if not gender_allowed(allocation.student, new_bed.room.floor.building.hostel):
            raise HostelError("The new hostel's gender policy does not match this student.")
        allocation.status, allocation.ended_at = AllocationStatus.TRANSFERRED, timezone.now()
        allocation.save(update_fields=["status", "ended_at", "updated_at"])
        new = _create_allocation(student=allocation.student, bed=new_bed, semester=allocation.semester,
                                 application=allocation.application, allocated_by=actor,
                                 status=AllocationStatus.ACTIVE)
        record_audit_event(actor, "HOSTEL.TRANSFERRED", new, ctx=ctx,
                           changes={"before": {"bed": str(allocation.bed)}, "after": {"bed": str(new_bed)}})
        notify(allocation.student.user, "HOSTEL", "Your room changed", f"You were moved to {new_bed}.",
               route_name="hostels:my_hostel")
    return new


def vacate(actor, allocation: HostelAllocation, ctx: RequestContext) -> HostelAllocation:
    authorize(actor, "can_manage_hostels", allocation, ctx=ctx)
    with transaction.atomic():
        allocation = HostelAllocation.objects.select_for_update(of=("self",)).select_related("student__user").get(
            pk=allocation.pk)
        if allocation.status not in HOLDING_ALLOCATION_STATUSES:
            raise HostelError("This allocation is not holding a bed.")
        allocation.status, allocation.ended_at = AllocationStatus.VACATED, timezone.now()
        allocation.save(update_fields=["status", "ended_at", "updated_at"])
        record_audit_event(actor, "HOSTEL.VACATED", allocation, ctx=ctx)
        notify(allocation.student.user, "HOSTEL", "Checked out", "Your hostel allocation has ended.",
               route_name="hostels:my_hostel")
    return allocation


def set_space_status(actor, obj, status: str, ctx: RequestContext):
    """Put a room or bed into maintenance/closed or back into service."""
    authorize(actor, "can_manage_hostels", obj, ctx=ctx)
    if status not in SpaceStatus.values:
        raise HostelError("Unknown status.")
    before = obj.status
    obj.status = status
    obj.save(update_fields=["status", "updated_at"])
    record_audit_event(actor, f"HOSTEL.{type(obj).__name__.upper()}_STATUS", obj, ctx=ctx,
                       changes={"before": {"status": before}, "after": {"status": status}})
    return obj


def save_hostel(actor, hostel, ctx: RequestContext):
    authorize(actor, "can_manage_hostels", hostel, ctx=ctx)
    created = hostel._state.adding
    hostel.full_clean()
    hostel.save()
    record_audit_event(actor, "HOSTEL.CREATED" if created else "HOSTEL.UPDATED", hostel, ctx=ctx,
                       changes={"after": {"code": hostel.code, "gender_policy": hostel.gender_policy,
                                          "active": hostel.is_active}})
    return hostel


@transaction.atomic
def add_rooms(actor, hostel, *, building: str, floor_level: int, first_room_number: int, rooms: int,
              room_type: str, beds_per_room: int, fee_per_semester, ctx: RequestContext) -> int:
    """Create a floor of identical rooms and their beds (labels A, B, C ...)."""
    from apps.hostels.models import HostelBuilding, HostelFloor, Room

    authorize(actor, "can_manage_hostels", hostel, ctx=ctx)
    building_obj, _ = HostelBuilding.objects.get_or_create(hostel=hostel, name=building.strip())
    floor, _ = HostelFloor.objects.get_or_create(building=building_obj, level=floor_level)
    created = 0
    for offset in range(rooms):
        number = str(first_room_number + offset)
        if Room.objects.filter(floor=floor, number=number).exists():
            raise HostelError(f"Room {number} already exists on that floor.")
        room = Room.objects.create(floor=floor, number=number, room_type=room_type, capacity=beds_per_room,
                                   fee_per_semester=fee_per_semester)
        Bed.objects.bulk_create(Bed(room=room, label=chr(ord("A") + i)) for i in range(beds_per_room))
        created += 1
    record_audit_event(actor, "HOSTEL.ROOMS_ADDED", hostel, ctx=ctx, changes={"after": {
        "building": building_obj.name, "floor": floor_level, "rooms": rooms, "beds_per_room": beds_per_room}})
    return created


def save_window(actor, window, ctx: RequestContext):
    authorize(actor, "can_manage_hostels", window, ctx=ctx)
    window.full_clean()
    window.save()
    record_audit_event(actor, "HOSTEL.WINDOW_SAVED", window, ctx=ctx, changes={"after": {
        "mode": window.mode, "opens": window.opens_at.isoformat(), "closes": window.closes_at.isoformat()}})
    return window

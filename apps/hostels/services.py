from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.hostels.models import Bed, HostelAllocation
from apps.academics.models import Semester
from apps.core.utils import log_audit_event

def book_hostel_bed(student_profile, bed_id, semester_id, actor=None, ip_address=None):
    """
    Atomically reserves a hostel bed for a student.
    Guarantees zero race conditions via select_for_update() row-level locking.
    Enforces gender policy and single-allocation constraints.
    """
    with transaction.atomic():
        # Lock bed row exclusively
        try:
            bed = Bed.objects.select_for_update().select_related('room__building__hostel').get(id=bed_id)
        except Bed.DoesNotExist:
            raise ValidationError("Specified bed was not found.")

        try:
            semester = Semester.objects.get(id=semester_id)
        except Semester.DoesNotExist:
            raise ValidationError("Academic semester was not found.")

        # 1. Availability check
        if bed.is_occupied or bed.status != 'AVAILABLE':
            raise ValidationError(f"Bed {bed.bed_number} in Room {bed.room.room_number} is no longer available.")

        hostel = bed.room.building.hostel
        if not hostel.is_active or not bed.room.is_active:
            raise ValidationError("This hostel room is currently unavailable for booking.")

        # 2. Gender policy check
        if hostel.gender_policy in ('MALE', 'FEMALE'):
            if student_profile.gender != hostel.gender_policy:
                raise ValidationError(
                    f"Gender mismatch: {hostel.name} is designated for {hostel.get_gender_policy_display()} students only."
                )

        # 3. Existing active allocation check
        active_alloc = HostelAllocation.objects.filter(
            student=student_profile,
            semester=semester,
            status='ACTIVE'
        ).first()
        if active_alloc:
            raise ValidationError(
                f"You already hold an active bed allocation ({active_alloc.bed.room.building.hostel.name}, "
                f"Room {active_alloc.bed.room.room_number}, Bed {active_alloc.bed.bed_number}) for this semester."
            )

        # 4. Mark bed occupied
        bed.is_occupied = True
        bed.status = 'OCCUPIED'
        bed.save(update_fields=['is_occupied', 'status'])

        # 5. Create allocation record
        allocation = HostelAllocation.objects.create(
            student=student_profile,
            bed=bed,
            semester=semester,
            status='ACTIVE'
        )

        # 6. Immutable audit log
        log_audit_event(
            actor=actor or student_profile.user,
            action='BOOK_HOSTEL_BED',
            target_model='HostelAllocation',
            target_id=str(allocation.id),
            changes={
                'hostel': hostel.name,
                'building': bed.room.building.name,
                'room': bed.room.room_number,
                'bed': bed.bed_number,
                'semester': str(semester)
            },
            ip_address=ip_address,
            details=f"Student {student_profile.student_id} reserved bed {bed.bed_number} in {hostel.name} Room {bed.room.room_number}."
        )

        return allocation


def cancel_hostel_allocation(student_profile, allocation_id, actor=None, ip_address=None):
    """
    Safely vacates and terminates an active hostel allocation within an atomic transaction.
    """
    with transaction.atomic():
        try:
            allocation = HostelAllocation.objects.select_for_update().get(
                id=allocation_id,
                student=student_profile,
                status='ACTIVE'
            )
        except HostelAllocation.DoesNotExist:
            raise ValidationError("Active hostel allocation not found.")

        bed = Bed.objects.select_for_update().get(id=allocation.bed_id)
        bed.is_occupied = False
        bed.status = 'AVAILABLE'
        bed.save(update_fields=['is_occupied', 'status'])

        allocation.status = 'TERMINATED'
        allocation.vacated_at = timezone.now()
        allocation.save(update_fields=['status', 'vacated_at'])

        log_audit_event(
            actor=actor or student_profile.user,
            action='VACATE_HOSTEL_BED',
            target_model='HostelAllocation',
            target_id=str(allocation.id),
            changes={'bed_id': str(bed.id), 'status': 'TERMINATED'},
            ip_address=ip_address,
            details=f"Student {student_profile.student_id} vacated bed {bed.bed_number}."
        )
        return allocation

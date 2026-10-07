from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.academics.models import Unit, Semester, UnitRegistration, UnitPrerequisite
from apps.core.utils import log_audit_event

MAX_SEMESTER_CREDIT_HOURS = 24

def register_student_unit(student_profile, unit_id, semester_id, actor=None, ip_address=None):
    """
    Performs transactional unit registration:
    1. Locks unit record with select_for_update() to prevent race conditions exceeding max_capacity
    2. Enforces registration window validity
    3. Checks unit active status
    4. Prevents duplicate active registration
    5. Checks prerequisites fulfillment
    6. Enforces maximum credit hour threshold
    7. Atomically creates registration record
    """
    with transaction.atomic():
        # Lock unit row to prevent over-capacity race condition
        try:
            unit = Unit.objects.select_for_update().get(id=unit_id)
        except Unit.DoesNotExist:
            raise ValidationError("Academic unit not found.")

        try:
            semester = Semester.objects.get(id=semester_id)
        except Semester.DoesNotExist:
            raise ValidationError("Academic semester not found.")

        # 1. Registration window check
        if not semester.registration_open:
            raise ValidationError("Unit registration is currently closed for this semester.")

        # 2. Unit active check
        if not unit.is_active:
            raise ValidationError(f"Unit {unit.code} is currently inactive and unavailable for registration.")

        # 3. Duplicate check
        existing_reg = UnitRegistration.objects.filter(
            student=student_profile,
            unit=unit,
            semester=semester,
            status='REGISTERED'
        ).first()
        if existing_reg:
            raise ValidationError(f"You are already registered for unit {unit.code} in this semester.")

        # 4. Prerequisite validation
        prereqs = UnitPrerequisite.objects.filter(unit=unit).select_related('prerequisite')
        for link in prereqs:
            has_passed = UnitRegistration.objects.filter(
                student=student_profile,
                unit=link.prerequisite,
                status='COMPLETED'
            ).exists()
            if not has_passed:
                raise ValidationError(
                    f"Cannot register for {unit.code}: Missing prerequisite {link.prerequisite.code} ({link.prerequisite.name})."
                )

        # 5. Credit hour limit check
        current_credits = 0
        active_regs = UnitRegistration.objects.filter(
            student=student_profile,
            semester=semester,
            status='REGISTERED'
        ).select_related('unit')
        for reg in active_regs:
            current_credits += reg.unit.credit_hours

        if current_credits + unit.credit_hours > MAX_SEMESTER_CREDIT_HOURS:
            raise ValidationError(
                f"Credit limit exceeded. Registering for {unit.code} ({unit.credit_hours} CH) would bring your total "
                f"to {current_credits + unit.credit_hours} CH. Maximum allowed is {MAX_SEMESTER_CREDIT_HOURS} CH."
            )

        # 6. Capacity check with locked row
        enrolled_count = UnitRegistration.objects.filter(
            unit=unit,
            semester=semester,
            status='REGISTERED'
        ).count()

        if enrolled_count >= unit.max_capacity:
            raise ValidationError(f"Registration full: Unit {unit.code} has reached its capacity of {unit.max_capacity} students.")

        # If previous dropped registration exists for this semester, reactivate or create new
        dropped_reg = UnitRegistration.objects.filter(
            student=student_profile,
            unit=unit,
            semester=semester,
            status='DROPPED'
        ).first()

        if dropped_reg:
            dropped_reg.status = 'REGISTERED'
            dropped_reg.dropped_at = None
            dropped_reg.save(update_fields=['status', 'dropped_at'])
            registration = dropped_reg
        else:
            registration = UnitRegistration.objects.create(
                student=student_profile,
                unit=unit,
                semester=semester,
                status='REGISTERED'
            )

        # Record immutable audit log
        log_audit_event(
            actor=actor or student_profile.user,
            action='REGISTER_UNIT',
            target_model='UnitRegistration',
            target_id=str(registration.id),
            changes={'unit': unit.code, 'semester': str(semester), 'credit_hours': unit.credit_hours},
            ip_address=ip_address,
            details=f"Student {student_profile.student_id} registered for {unit.code}"
        )

        return registration


def drop_student_unit(student_profile, registration_id, actor=None, ip_address=None):
    """
    Safely drops a registered unit within an atomic transaction.
    """
    with transaction.atomic():
        try:
            reg = UnitRegistration.objects.select_for_update().get(
                id=registration_id,
                student=student_profile,
                status='REGISTERED'
            )
        except UnitRegistration.DoesNotExist:
            raise ValidationError("Active registration record not found.")

        if not reg.semester.registration_open:
            raise ValidationError("Cannot drop unit: the registration period for this semester has closed.")

        reg.status = 'DROPPED'
        reg.dropped_at = timezone.now()
        reg.save(update_fields=['status', 'dropped_at'])

        log_audit_event(
            actor=actor or student_profile.user,
            action='DROP_UNIT',
            target_model='UnitRegistration',
            target_id=str(reg.id),
            changes={'unit': reg.unit.code, 'semester': str(reg.semester)},
            ip_address=ip_address,
            details=f"Student {student_profile.student_id} dropped {reg.unit.code}"
        )
        return reg

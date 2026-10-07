from django.core.exceptions import ValidationError
from django.db.models import Q
from apps.timetable.models import TimetableEntry

def validate_timetable_entry(unit, classroom, lecturer, semester, day_of_week, start_time, end_time, exclude_id=None):
    """
    Validates that a timetable entry does not produce clashes:
    1. Valid time range: start_time < end_time
    2. Room conflict: No other session in this classroom on the same day during overlapping hours
    3. Lecturer conflict: If lecturer assigned, they cannot teach elsewhere during overlapping hours
    """
    if start_time >= end_time:
        raise ValidationError("Class start time must be before end time.")

    # Time overlap condition: (ExistingStart < NewEnd) AND (ExistingEnd > NewStart)
    overlap_condition = Q(
        semester=semester,
        day_of_week=day_of_week,
        start_time__lt=end_time,
        end_time__gt=start_time
    )

    # 1. Room Conflict Check
    room_conflicts = TimetableEntry.objects.filter(classroom=classroom).filter(overlap_condition)
    if exclude_id:
        room_conflicts = room_conflicts.exclude(id=exclude_id)

    if room_conflicts.exists():
        clash = room_conflicts.first()
        raise ValidationError(
            f"Classroom Conflict: {classroom.code} is already booked for unit {clash.unit.code} "
            f"on {clash.get_day_of_week_display()} from {clash.start_time.strftime('%H:%M')} to {clash.end_time.strftime('%H:%M')}."
        )

    # 2. Lecturer Conflict Check
    if lecturer:
        lecturer_conflicts = TimetableEntry.objects.filter(lecturer=lecturer).filter(overlap_condition)
        if exclude_id:
            lecturer_conflicts = lecturer_conflicts.exclude(id=exclude_id)

        if lecturer_conflicts.exists():
            clash = lecturer_conflicts.first()
            raise ValidationError(
                f"Lecturer Conflict: {lecturer.user.full_name} is already scheduled to teach {clash.unit.code} "
                f"in room {clash.classroom.code} on {clash.get_day_of_week_display()} from {clash.start_time.strftime('%H:%M')} to {clash.end_time.strftime('%H:%M')}."
            )

    return True

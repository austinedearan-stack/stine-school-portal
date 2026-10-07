from collections import defaultdict
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from apps.timetable.models import TimetableEntry, Classroom
from apps.academics.models import Semester, UnitRegistration
from apps.core.permissions import ROLE_STUDENT

DAYS_ORDER = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
DAY_LABELS = {
    'MON': 'Monday',
    'TUE': 'Tuesday',
    'WED': 'Wednesday',
    'THU': 'Thursday',
    'FRI': 'Friday',
    'SAT': 'Saturday',
    'SUN': 'Sunday',
}

@login_required
def student_timetable_view(request):
    """
    Renders the authenticated student's personalized weekly timetable.
    Identity is derived strictly from request.user context.
    """
    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        return redirect('timetable:master')

    current_semester = Semester.objects.filter(is_current=True).first()
    schedule_by_day = {day: [] for day in DAYS_ORDER}

    if current_semester:
        registered_unit_ids = UnitRegistration.objects.filter(
            student=request.user.student_profile,
            semester=current_semester,
            status='REGISTERED'
        ).values_list('unit_id', flat=True)

        entries = TimetableEntry.objects.filter(
            semester=current_semester,
            unit_id__in=registered_unit_ids
        ).select_related('unit', 'classroom', 'lecturer__user').order_by('start_time')

        for entry in entries:
            if entry.day_of_week in schedule_by_day:
                schedule_by_day[entry.day_of_week].append(entry)

    day_schedule_list = [
        {'code': day, 'label': DAY_LABELS[day], 'entries': schedule_by_day[day]}
        for day in DAYS_ORDER
    ]

    return render(request, 'timetable/student_timetable.html', {
        'day_schedules': day_schedule_list,
        'current_semester': current_semester,
    })


@login_required
def master_timetable_view(request):
    """
    General campus timetable with search and building filter.
    """
    current_semester = Semester.objects.filter(is_current=True).first()
    query = request.GET.get('q', '').strip()
    day_filter = request.GET.get('day', '').strip()

    entries = TimetableEntry.objects.all().select_related('unit', 'classroom', 'lecturer__user', 'semester')
    if current_semester:
        entries = entries.filter(semester=current_semester)

    if query:
        entries = entries.filter(unit__code__icontains=query) | entries.filter(classroom__code__icontains=query)
    if day_filter:
        entries = entries.filter(day_of_week=day_filter)

    return render(request, 'timetable/master_timetable.html', {
        'entries': entries.order_by('day_of_week', 'start_time')[:100],
        'current_semester': current_semester,
        'days': DAY_LABELS,
        'selected_day': day_filter,
        'query': query,
    })

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.academics.models import Department, Semester, Unit, UnitRegistration
from apps.academics.services import drop_student_unit, register_student_unit
from apps.core.permissions import ROLE_STUDENT
from apps.core.utils import get_client_ip


@login_required
def unit_catalog_view(request):
    """
    Unit catalog with search, department filters, and seat availability.
    """
    query = request.GET.get('q', '').strip()
    department_code = request.GET.get('dept', '').strip()

    units = Unit.objects.filter(is_active=True).select_related('department', 'lecturer__user').prefetch_related('prerequisite_links__prerequisite')

    if query:
        units = units.filter(Q(code__icontains=query) | Q(name__icontains=query))
    if department_code:
        units = units.filter(department__code=department_code)

    departments = Department.objects.all()
    current_semester = Semester.objects.filter(is_current=True).first()

    # Pre-fetch registered unit IDs if student
    registered_unit_ids = set()
    if request.user.role == ROLE_STUDENT and hasattr(request.user, 'student_profile') and current_semester:
        registered_unit_ids = set(
            UnitRegistration.objects.filter(
                student=request.user.student_profile,
                semester=current_semester,
                status='REGISTERED'
            ).values_list('unit_id', flat=True)
        )

    return render(request, 'academics/catalog.html', {
        'units': units,
        'departments': departments,
        'selected_dept': department_code,
        'query': query,
        'current_semester': current_semester,
        'registered_unit_ids': registered_unit_ids,
    })


@login_required
def unit_detail_view(request, unit_code):
    unit = get_object_or_404(
        Unit.objects.select_related('department', 'lecturer__user').prefetch_related('prerequisite_links__prerequisite'),
        code=unit_code
    )
    current_semester = Semester.objects.filter(is_current=True).first()
    is_registered = False

    if request.user.role == ROLE_STUDENT and hasattr(request.user, 'student_profile') and current_semester:
        is_registered = UnitRegistration.objects.filter(
            student=request.user.student_profile,
            unit=unit,
            semester=current_semester,
            status='REGISTERED'
        ).exists()

    return render(request, 'academics/unit_detail.html', {
        'unit': unit,
        'current_semester': current_semester,
        'is_registered': is_registered,
    })


@login_required
def register_unit_view(request):
    """
    POST handler for student unit registration.
    Enforces authorization, CSRF, and atomic race-condition safety.
    """
    if request.method != 'POST':
        return redirect('academics:catalog')

    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only authenticated students can register for units.")

    unit_id = request.POST.get('unit_id')
    current_semester = Semester.objects.filter(is_current=True).first()

    if not current_semester:
        messages.error(request, "No active academic semester is currently configured for registration.")
        return redirect('academics:catalog')

    try:
        register_student_unit(
            student_profile=request.user.student_profile,
            unit_id=unit_id,
            semester_id=current_semester.id,
            actor=request.user,
            ip_address=get_client_ip(request)
        )
        messages.success(request, "Successfully registered for unit!")
    except ValidationError as e:
        messages.error(request, e.message)
    except Exception:
        messages.error(request, "An unexpected error occurred during registration. Please try again.")

    return redirect('academics:my_units')


@login_required
def drop_unit_view(request):
    if request.method != 'POST':
        return redirect('academics:my_units')

    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only students can drop units.")

    registration_id = request.POST.get('registration_id')

    try:
        drop_student_unit(
            student_profile=request.user.student_profile,
            registration_id=registration_id,
            actor=request.user,
            ip_address=get_client_ip(request)
        )
        messages.success(request, "Unit dropped successfully.")
    except ValidationError as e:
        messages.error(request, e.message)
    except Exception:
        messages.error(request, "Unable to drop unit at this time.")

    return redirect('academics:my_units')


@login_required
def my_units_view(request):
    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        return redirect('core:dashboard')

    current_semester = Semester.objects.filter(is_current=True).first()

    current_registrations = UnitRegistration.objects.filter(
        student=request.user.student_profile,
        semester=current_semester,
        status='REGISTERED'
    ).select_related('unit__department', 'unit__lecturer__user') if current_semester else []

    total_current_credits = sum(reg.unit.credit_hours for reg in current_registrations)

    past_registrations = UnitRegistration.objects.filter(
        student=request.user.student_profile
    ).exclude(
        semester=current_semester
    ).select_related('unit', 'semester') if current_semester else UnitRegistration.objects.filter(student=request.user.student_profile)

    return render(request, 'academics/my_units.html', {
        'current_registrations': current_registrations,
        'past_registrations': past_registrations,
        'current_semester': current_semester,
        'total_current_credits': total_current_credits,
    })

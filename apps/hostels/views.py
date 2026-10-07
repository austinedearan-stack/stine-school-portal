from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.academics.models import Semester
from apps.core.permissions import ROLE_STUDENT
from apps.core.utils import get_client_ip
from apps.hostels.models import Hostel, HostelAllocation, Room
from apps.hostels.services import book_hostel_bed, cancel_hostel_allocation


@login_required
def hostel_catalog_view(request):
    """
    Browse available hostels and vacancy summary.
    """
    hostels = Hostel.objects.filter(is_active=True).prefetch_related('buildings__rooms__beds')
    current_semester = Semester.objects.filter(is_current=True).first()

    my_allocation = None
    if request.user.role == ROLE_STUDENT and hasattr(request.user, 'student_profile') and current_semester:
        my_allocation = HostelAllocation.objects.filter(
            student=request.user.student_profile,
            semester=current_semester,
            status='ACTIVE'
        ).select_related('bed__room__building__hostel').first()

    return render(request, 'hostels/catalog.html', {
        'hostels': hostels,
        'current_semester': current_semester,
        'my_allocation': my_allocation,
    })


@login_required
def hostel_rooms_view(request, hostel_code):
    hostel = get_object_or_404(Hostel, code=hostel_code, is_active=True)
    rooms = Room.objects.filter(building__hostel=hostel, is_active=True).prefetch_related('beds', 'building')
    current_semester = Semester.objects.filter(is_current=True).first()

    return render(request, 'hostels/hostel_rooms.html', {
        'hostel': hostel,
        'rooms': rooms,
        'current_semester': current_semester,
    })


@login_required
def book_bed_view(request):
    if request.method != 'POST':
        return redirect('hostels:catalog')

    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only students are eligible to book hostel beds.")

    bed_id = request.POST.get('bed_id')
    current_semester = Semester.objects.filter(is_current=True).first()

    if not current_semester:
        messages.error(request, "Hostel booking is unavailable: no active academic semester is configured.")
        return redirect('hostels:catalog')

    try:
        allocation = book_hostel_bed(
            student_profile=request.user.student_profile,
            bed_id=bed_id,
            semester_id=current_semester.id,
            actor=request.user,
            ip_address=get_client_ip(request)
        )
        messages.success(
            request,
            f"Successfully booked Bed {allocation.bed.bed_number} in Room {allocation.bed.room.room_number}!"
        )
    except ValidationError as e:
        messages.error(request, e.message)
    except Exception:
        messages.error(request, "Unable to complete bed reservation. Please try again.")

    return redirect('hostels:my_hostel')


@login_required
def my_hostel_view(request):
    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        return redirect('hostels:catalog')

    current_semester = Semester.objects.filter(is_current=True).first()
    allocation = None
    roommates = []

    if current_semester:
        allocation = HostelAllocation.objects.filter(
            student=request.user.student_profile,
            semester=current_semester,
            status='ACTIVE'
        ).select_related('bed__room__building__hostel').first()

        if allocation:
            # Find other students assigned to the same room
            roommates = HostelAllocation.objects.filter(
                bed__room=allocation.bed.room,
                semester=current_semester,
                status='ACTIVE'
            ).exclude(student=request.user.student_profile).select_related('student__user', 'bed')

    past_allocations = HostelAllocation.objects.filter(
        student=request.user.student_profile,
        status='TERMINATED'
    ).select_related('bed__room__building__hostel', 'semester')

    return render(request, 'hostels/my_hostel.html', {
        'allocation': allocation,
        'roommates': roommates,
        'past_allocations': past_allocations,
        'current_semester': current_semester,
    })


@login_required
def cancel_allocation_view(request):
    if request.method != 'POST':
        return redirect('hostels:my_hostel')

    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only students can cancel their hostel reservation.")

    allocation_id = request.POST.get('allocation_id')

    try:
        cancel_hostel_allocation(
            student_profile=request.user.student_profile,
            allocation_id=allocation_id,
            actor=request.user,
            ip_address=get_client_ip(request)
        )
        messages.success(request, "Hostel bed allocation has been cancelled.")
    except ValidationError as e:
        messages.error(request, e.message)
    except Exception:
        messages.error(request, "Failed to cancel hostel allocation.")

    return redirect('hostels:catalog')

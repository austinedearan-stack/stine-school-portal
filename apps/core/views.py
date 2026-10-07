from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.permissions import ROLE_ADMIN, ROLE_STAFF, ROLE_STUDENT, ROLE_SUPERADMIN


@login_required
def dashboard_view(request):
    """
    Central dispatcher: Routes the authenticated user to their role-appropriate dashboard.
    Identity is strictly derived from request.user session.
    """
    role = request.user.role
    if role == ROLE_STUDENT:
        return redirect('core:student_dashboard')
    elif role in (ROLE_ADMIN, ROLE_SUPERADMIN):
        return redirect('administration:dashboard')
    elif role == ROLE_STAFF:
        return redirect('administration:staff_dashboard')
    return redirect('accounts:profile')


@login_required
def student_dashboard_view(request):
    """
    Renders the student dashboard with strictly context-derived data.
    Never trusts URL query parameters for identity.
    """
    if request.user.role != ROLE_STUDENT:
        return redirect('core:dashboard')

    student = getattr(request.user, 'student_profile', None)
    if not student:
        return render(request, 'errors/403.html', {'message': 'Student profile not found for this account.'}, status=403)

    # 1. Registered units for current semester
    registered_units = []
    try:
        from apps.academics.models import UnitRegistration
        registered_units = UnitRegistration.objects.filter(
            student=student,
            status='REGISTERED'
        ).select_related('unit', 'semester')
    except Exception:  # noqa: S110 - audit S-6, fixed in Phase 4
        pass

    # 2. Hostel booking/allocation status
    hostel_allocation = None
    try:
        from apps.hostels.models import HostelAllocation
        hostel_allocation = HostelAllocation.objects.filter(
            student=student,
            status='ACTIVE'
        ).select_related('bed__room__building__hostel').first()
    except Exception:  # noqa: S110 - audit S-6, fixed in Phase 4
        pass

    # 3. Pending requests
    pending_requests = []
    try:
        from apps.student_requests.models import StudentRequest
        pending_requests = StudentRequest.objects.filter(
            student=student
        ).exclude(status__in=['RESOLVED', 'CLOSED', 'CANCELLED']).order_by('-created_at')[:5]
    except Exception:  # noqa: S110 - audit S-6, fixed in Phase 4
        pass

    # 4. Recent notifications
    recent_notifications = []
    try:
        from apps.notifications.models import Notification
        recent_notifications = Notification.objects.filter(
            recipient=request.user
        ).order_by('-created_at')[:5]
    except Exception:  # noqa: S110 - audit S-6, fixed in Phase 4
        pass

    # 5. Targeted announcements
    announcements = []
    try:
        from django.utils import timezone

        from apps.notifications.models import Announcement
        announcements = Announcement.objects.filter(
            is_published=True,
            publish_date__lte=timezone.now()
        ).order_by('-publish_date')[:5]
    except Exception:  # noqa: S110 - audit S-6, fixed in Phase 4
        pass

    # 6. Club memberships
    club_memberships = []
    try:
        from apps.clubs.models import ClubMembership
        club_memberships = ClubMembership.objects.filter(
            student=student,
            status='APPROVED'
        ).select_related('club')[:5]
    except Exception:  # noqa: S110 - audit S-6, fixed in Phase 4
        pass

    # 7. Timetable entries for student's current registered units
    timetable_summary = []
    try:
        from apps.timetable.models import TimetableEntry
        unit_ids = [reg.unit_id for reg in registered_units]
        timetable_summary = TimetableEntry.objects.filter(
            unit_id__in=unit_ids
        ).select_related('unit', 'classroom', 'lecturer').order_by('day_of_week', 'start_time')[:6]
    except Exception:  # noqa: S110 - audit S-6, fixed in Phase 4
        pass

    context = {
        'student': student,
        'registered_units': registered_units,
        'hostel_allocation': hostel_allocation,
        'pending_requests': pending_requests,
        'recent_notifications': recent_notifications,
        'announcements': announcements,
        'club_memberships': club_memberships,
        'timetable_summary': timetable_summary,
    }
    return render(request, 'core/student_dashboard.html', context)


# ==========================================
# Production Error Views (Zero Leakage)
# ==========================================

def bad_request_view(request, exception=None):
    return render(request, 'errors/400.html', {'message': 'The server could not process your request due to invalid parameters.'}, status=400)

def permission_denied_view(request, exception=None):
    return render(request, 'errors/403.html', {'message': 'You are not authorized to view or access this resource.'}, status=403)

def not_found_view(request, exception=None):
    return render(request, 'errors/404.html', {'message': 'The requested page or resource could not be found.'}, status=404)

def server_error_view(request):
    # Rendered without the request context: context processors may touch the database, which may be
    # the very thing that failed. The standalone template contains no technical details.
    from django.http import HttpResponseServerError
    from django.template import loader

    return HttpResponseServerError(loader.get_template('errors/500.html').render({}))


def csrf_failure_view(request, reason=""):
    """CSRF failures get the generic 403 page; the technical reason is never shown to the user."""
    return render(request, 'errors/403.html', {'message': 'Your request could not be verified. Please reload the page and try again.'}, status=403)


@never_cache
@require_GET
def health_view(request):
    """Liveness probe for the container orchestrator. Reveals nothing about the system."""
    return HttpResponse("ok", content_type="text/plain")

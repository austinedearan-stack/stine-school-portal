from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.permissions import ADMIN_ROLES, ROLE_STAFF, ROLE_STUDENT
from apps.notifications.models import Announcement, Notification


@login_required
def notification_list_view(request):
    notifications = Notification.objects.filter(recipient=request.user).order_by('-created_at')

    # Optional: mark one notification as read if requested via GET param
    mark_id = request.GET.get('read')
    if mark_id:
        Notification.objects.filter(id=mark_id, recipient=request.user).update(is_read=True)
        return redirect('notifications:list')

    return render(request, 'notifications/notification_list.html', {
        'notifications': notifications,
    })


@login_required
def mark_all_read_view(request):
    if request.method == 'POST':
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        messages.success(request, "All notifications marked as read.")
    return redirect('notifications:list')


@login_required
def announcement_list_view(request):
    """
    Renders announcements matching the caller's authorized audience.
    """
    now = timezone.now()
    qs = Announcement.objects.filter(
        is_published=True,
        publish_date__lte=now
    ).filter(
        Q(expiration_date__isnull=True) | Q(expiration_date__gte=now)
    )

    if request.user.role in ADMIN_ROLES:
        # Admins can view all announcements
        pass
    elif request.user.role == ROLE_STUDENT and hasattr(request.user, 'student_profile'):
        student = request.user.student_profile
        qs = qs.filter(
            Q(audience='ALL') |
            Q(audience='STUDENTS') |
            Q(department=student.program.department) |
            Q(faculty=student.program.department.faculty)
        )
    elif request.user.role == ROLE_STAFF and hasattr(request.user, 'staff_profile'):
        staff = request.user.staff_profile
        qs = qs.filter(
            Q(audience='ALL') |
            Q(audience='STAFF') |
            Q(department=staff.department) |
            Q(faculty=staff.department.faculty)
        )
    else:
        qs = qs.filter(audience='ALL')

    return render(request, 'notifications/announcement_list.html', {
        'announcements': qs.select_related('author'),
    })


@login_required
def announcement_detail_view(request, announcement_id):
    announcement = get_object_or_404(Announcement, id=announcement_id, is_published=True)
    return render(request, 'notifications/announcement_detail.html', {
        'announcement': announcement,
    })

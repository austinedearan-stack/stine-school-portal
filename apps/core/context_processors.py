def portal_context(request):
    """
    Context processor injecting portal-wide metadata, role booleans, and notification badge counts.
    """
    context = {
        'portal_name': 'University Student Information Portal',
        'is_student': False,
        'is_staff': False,
        'is_admin': False,
        'is_superadmin': False,
        'unread_notifications_count': 0,
    }

    if request.user.is_authenticated:
        role = getattr(request.user, 'role', '')
        context['current_role'] = role
        context['is_student'] = (role == 'STUDENT')
        context['is_staff'] = (role == 'STAFF')
        context['is_admin'] = (role == 'ADMIN')
        context['is_superadmin'] = (role == 'SUPERADMIN')

        # Count unread notifications if app is loaded
        try:
            from apps.notifications.models import Notification
            context['unread_notifications_count'] = Notification.objects.filter(
                recipient=request.user,
                is_read=False
            ).count()
        except Exception:
            context['unread_notifications_count'] = 0

    return context

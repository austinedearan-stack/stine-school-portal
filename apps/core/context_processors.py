def portal_context(request):
    """Portal-wide template context: role flags, navigation and the unread-notification badge."""
    context = {"portal_name": "University Student Portal", "navigation": [], "unread_notifications_count": 0}
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return context

    from apps.core.navigation import navigation_for
    from apps.notifications.models import Notification

    context.update(
        current_role=user.role,
        navigation=navigation_for(user),
        unread_notifications_count=Notification.objects.filter(recipient=user, read_at__isnull=True).count(),
    )
    return context

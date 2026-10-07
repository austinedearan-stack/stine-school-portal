from apps.notifications.models import Notification


def send_in_app_notification(recipient, title, message, notification_type='SYSTEM', link=''):
    """
    Creates an in-app notification record for the user.
    """
    return Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        message=message,
        link=link
    )

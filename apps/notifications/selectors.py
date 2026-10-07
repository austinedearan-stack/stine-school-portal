"""Notification read queries: a user only ever sees their own notifications."""

from __future__ import annotations

from apps.notifications.models import Notification


def notifications_for(user):
    return Notification.objects.filter(recipient=user).order_by("-created_at")


def unread_count(user) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()

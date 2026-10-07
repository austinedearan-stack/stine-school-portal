"""Notification creation. Notifications never contain sensitive data and never store URLs: a link is a
route name + kwargs resolved with reverse() when rendered (no open redirect, no SSRF surface)."""

from __future__ import annotations

from collections.abc import Iterable

from apps.notifications.models import Notification, NotificationKind


def notify(recipient, kind: str, title: str, body: str = "", *, route_name: str = "", route_kwargs: dict | None = None):
    if recipient is None:
        return None
    if kind not in NotificationKind.values:
        raise ValueError(f"unknown notification kind {kind}")
    return Notification.objects.create(
        recipient=recipient, kind=kind, title=title[:200], body=body[:1000], route_name=route_name,
        route_kwargs=route_kwargs or {},
    )


def notify_many(recipients: Iterable, kind: str, title: str, body: str = "", **kwargs) -> int:
    created = 0
    for recipient in recipients:
        if notify(recipient, kind, title, body, **kwargs):
            created += 1
    return created

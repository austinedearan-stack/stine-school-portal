"""Notifications and announcements (ARCHITECTURE.md §5.3; audit Z-1, Z-2, Z-10).

* Notifications never contain sensitive data and never store URLs: a link is a route name + kwargs,
  resolved with reverse() from an allowlist when displayed (no open redirect, no SSRF surface).
* Read/unread changes are POST-only.
* Announcements are delivered to exactly the audience computed by ``selectors.audience_users``;
  future-dated ones are delivered by ``manage.py publish_due_announcements``.
* Optional email copies go to an outbox and are sent by ``manage.py send_outbox``, never in the request.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.notifications.models import (
    Announcement,
    AnnouncementAttachment,
    AnnouncementStatus,
    Notification,
    NotificationKind,
    OutboundEmail,
)

# Only these routes may be linked from a notification.
LINKABLE_ROUTES = {
    "accounts:profile", "accounts:security", "academics:my_units", "timetable:my_timetable", "hostels:my_hostel",
    "clubs:detail", "clubs:members", "requests:detail", "notifications:announcement", "core:dashboard",
}


class AnnouncementError(ValidationError):
    pass


def _email_kinds() -> set[str]:
    return set(getattr(settings, "NOTIFICATION_EMAIL_KINDS", ()))


def _queue_email(recipient, title: str, body: str) -> None:
    OutboundEmail.objects.create(recipient=recipient, subject=title[:200],
                                 body=(body or title)[:1800] + "\n\nSign in to the student portal for details.")


def notify(recipient, kind: str, title: str, body: str = "", *, route_name: str = "", route_kwargs: dict | None = None):
    if recipient is None:
        return None
    if kind not in NotificationKind.values:
        raise ValueError(f"unknown notification kind {kind}")
    if route_name and route_name not in LINKABLE_ROUTES:
        raise ValueError(f"route {route_name} is not linkable from notifications")
    notification = Notification.objects.create(
        recipient=recipient, kind=kind, title=title[:200], body=body[:1000], route_name=route_name,
        route_kwargs=route_kwargs or {},
    )
    if kind in _email_kinds():
        _queue_email(recipient, title, body)
    return notification


def notify_many(recipients: Iterable, kind: str, title: str, body: str = "", **kwargs) -> int:
    created = 0
    for recipient in recipients:
        if notify(recipient, kind, title, body, **kwargs):
            created += 1
    return created


def link_for(notification: Notification) -> str | None:
    if not notification.route_name or notification.route_name not in LINKABLE_ROUTES:
        return None
    try:
        return reverse(notification.route_name, kwargs=notification.route_kwargs or None)
    except NoReverseMatch:
        return None


def mark_read(user, notification_id) -> Notification | None:
    notification = Notification.objects.filter(pk=notification_id, recipient=user).first()
    if notification is not None and notification.read_at is None:
        Notification.objects.filter(pk=notification.pk).update(read_at=timezone.now())
    return notification


def mark_all_read(user) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).update(read_at=timezone.now())


# --- Announcements --------------------------------------------------------------------------------


def _validate(announcement: Announcement) -> None:
    if announcement.expires_at and announcement.expires_at <= announcement.publish_at:
        raise AnnouncementError("The expiry must be after the publish time.")
    announcement.full_clean(exclude=["recipients"])


def save_announcement(actor, announcement: Announcement, ctx, *, recipients=None, uploads=(),
                      publish: bool = False) -> Announcement:
    from apps.core import files
    from apps.core.audit import record_audit_event, record_security_event
    from apps.core.authz import authorize
    from apps.core.models import FilePurpose, SecurityEventType

    creating = announcement._state.adding
    if not creating:
        authorize(actor, "can_manage_announcement", announcement, ctx=ctx)
    else:
        announcement.author = actor
    if recipients is not None:
        announcement._pending_recipients = list(recipients)
    authorize(actor, "can_publish_announcement", announcement, ctx=ctx)
    if announcement.scope == "INDIVIDUAL" and not (recipients or (not creating and announcement.recipients.exists())):
        raise AnnouncementError("Name at least one recipient.")
    if publish:
        announcement.status = AnnouncementStatus.PUBLISHED
    _validate(announcement)
    cleaned = []
    for upload in uploads or []:
        try:
            cleaned.append(files.validate_upload(upload, FilePurpose.ANNOUNCEMENT_ATTACHMENT))
        except ValidationError as exc:
            record_security_event(SecurityEventType.UPLOAD_REJECTED, ctx=ctx, user=actor,
                                  details={"purpose": FilePurpose.ANNOUNCEMENT_ATTACHMENT, "reason": exc.messages[0]})
            raise AnnouncementError(f"{files.sanitise_filename(upload.name)}: {exc.messages[0]}") from exc
    with transaction.atomic():
        announcement.save()
        if recipients is not None:
            announcement.recipients.set(recipients)
        for clean in cleaned:
            AnnouncementAttachment.objects.create(
                announcement=announcement,
                file=files.store(clean, owner=actor, purpose=FilePurpose.ANNOUNCEMENT_ATTACHMENT))
        record_audit_event(actor, "ANNOUNCEMENT.CREATED" if creating else "ANNOUNCEMENT.UPDATED", announcement,
                           ctx=ctx, changes={"after": {"status": announcement.status, "scope": announcement.scope,
                                                       "audience": announcement.audience_roles}})
        if announcement.status == AnnouncementStatus.PUBLISHED and announcement.publish_at <= timezone.now():
            deliver(announcement)
    return announcement


def deliver(announcement: Announcement) -> int:
    """Notify the audience once (idempotent: guarded by notified_at under a row lock)."""
    from apps.notifications.selectors import audience_users

    with transaction.atomic():
        locked = Announcement.objects.select_for_update().get(pk=announcement.pk)
        if locked.notified_at is not None or locked.status != AnnouncementStatus.PUBLISHED:
            return 0
        users = list(audience_users(locked))
        kwargs = {"route_name": "notifications:announcement", "route_kwargs": {"announcement_id": str(locked.pk)}}
        Notification.objects.bulk_create(
            [Notification(recipient=u, kind=NotificationKind.ANNOUNCEMENT, title=locked.title[:200],
                          body=locked.body[:300], **kwargs) for u in users], batch_size=500)
        if NotificationKind.ANNOUNCEMENT in _email_kinds():
            OutboundEmail.objects.bulk_create(
                [OutboundEmail(recipient=u, subject=locked.title[:200], body=locked.body[:1800]) for u in users],
                batch_size=500)
        locked.notified_at = timezone.now()
        locked.save(update_fields=["notified_at", "updated_at"])
        return len(users)


def withdraw(actor, announcement: Announcement, ctx) -> Announcement:
    from apps.core.audit import record_audit_event
    from apps.core.authz import authorize

    authorize(actor, "can_manage_announcement", announcement, ctx=ctx)
    announcement.status = AnnouncementStatus.WITHDRAWN
    announcement.save(update_fields=["status", "updated_at"])
    record_audit_event(actor, "ANNOUNCEMENT.WITHDRAWN", announcement, ctx=ctx)
    return announcement


def deliver_due() -> int:
    due = Announcement.objects.filter(status=AnnouncementStatus.PUBLISHED, notified_at__isnull=True,
                                      publish_at__lte=timezone.now())
    return sum(deliver(a) for a in due)


def send_outbox(limit: int = 200) -> tuple[int, int]:
    """Send pending emails; each message is retried up to 5 times. Returns (sent, failed)."""
    from django.core.mail import send_mail

    sent = failed = 0
    pending = OutboundEmail.objects.filter(sent_at__isnull=True, attempts__lt=5).select_related("recipient")[:limit]
    for message in pending:
        try:
            send_mail(settings.EMAIL_SUBJECT_PREFIX + message.subject, message.body, settings.DEFAULT_FROM_EMAIL,
                      [message.recipient.email], fail_silently=False)
            OutboundEmail.objects.filter(pk=message.pk).update(sent_at=timezone.now(), attempts=message.attempts + 1)
            sent += 1
        except Exception as exc:  # delivery errors are recorded and retried later
            OutboundEmail.objects.filter(pk=message.pk).update(attempts=message.attempts + 1,
                                                               last_error=type(exc).__name__[:200])
            failed += 1
    return sent, failed

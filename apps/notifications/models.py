"""In-app notifications and targeted announcements (DATABASE.md §2.7)."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel


class NotificationKind(models.TextChoices):
    REQUEST_STATUS = "REQUEST_STATUS", "Request status"
    REQUEST_RESPONSE = "REQUEST_RESPONSE", "Request response"
    HOSTEL = "HOSTEL", "Hostel"
    REGISTRATION = "REGISTRATION", "Unit registration"
    TIMETABLE = "TIMETABLE", "Timetable"
    CLUB = "CLUB", "Clubs"
    ANNOUNCEMENT = "ANNOUNCEMENT", "Announcement"
    SECURITY = "SECURITY", "Security"
    SYSTEM = "SYSTEM", "System"


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=NotificationKind.choices)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=1000, blank=True)  # never sensitive data
    # Links are stored as a route name + kwargs and resolved with reverse() at render time:
    # no stored URLs, so a notification can never become an open redirect.
    route_name = models.CharField(max_length=100, blank=True)
    route_kwargs = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "read_at", "created_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=NotificationKind.values), name="notification_kind_valid")
        ]

    def __str__(self):
        return f"{self.recipient_id}: {self.title}"


class Audience(models.TextChoices):
    EVERYONE = "EVERYONE", "Everyone"
    STUDENTS = "STUDENTS", "Students"
    STAFF = "STAFF", "Staff"


class Scope(models.TextChoices):
    UNIVERSITY = "UNIVERSITY", "Whole university"
    FACULTY = "FACULTY", "Faculty"
    DEPARTMENT = "DEPARTMENT", "Department"
    PROGRAM = "PROGRAM", "Program"
    OFFERING = "OFFERING", "Unit offering"
    CLUB = "CLUB", "Club"
    INDIVIDUAL = "INDIVIDUAL", "Selected individuals"


class AnnouncementStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"


def _only(**set_fields):
    """Q requiring exactly the given scope FK to be set and every other scope FK to be null."""
    fks = ["faculty", "department", "program", "offering", "club"]
    conditions = {f"{fk}__isnull": fk not in set_fields for fk in fks}
    return Q(**conditions)


class Announcement(TimeStampedModel):
    title = models.CharField(max_length=200)
    body = models.TextField(max_length=10_000)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="announcements")
    audience_roles = models.CharField(max_length=10, choices=Audience.choices, default=Audience.EVERYONE)
    scope = models.CharField(max_length=12, choices=Scope.choices, default=Scope.UNIVERSITY)
    faculty = models.ForeignKey("academics.Faculty", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey(
        "academics.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    program = models.ForeignKey("academics.Program", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    offering = models.ForeignKey(
        "academics.UnitOffering", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    club = models.ForeignKey("clubs.Club", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    recipients = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="targeted_announcements")
    publish_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=AnnouncementStatus.choices, default=AnnouncementStatus.DRAFT)

    class Meta:
        ordering = ["-publish_at"]
        indexes = [models.Index(fields=["status", "publish_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(audience_roles__in=Audience.values), name="announcement_audience_valid"),
            models.CheckConstraint(condition=Q(status__in=AnnouncementStatus.values), name="announcement_status_valid"),
            models.CheckConstraint(
                condition=Q(expires_at__isnull=True) | Q(publish_at__lt=F("expires_at")),
                name="announcement_publish_before_expiry",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(scope__in=[Scope.UNIVERSITY, Scope.INDIVIDUAL]) & _only())
                    | (Q(scope=Scope.FACULTY) & _only(faculty=True))
                    | (Q(scope=Scope.DEPARTMENT) & _only(department=True))
                    | (Q(scope=Scope.PROGRAM) & _only(program=True))
                    | (Q(scope=Scope.OFFERING) & _only(offering=True))
                    | (Q(scope=Scope.CLUB) & _only(club=True))
                ),
                name="announcement_scope_target_matches",
            ),
        ]

    def __str__(self):
        return self.title


class AnnouncementAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="attachments")
    file = models.OneToOneField("core.StoredFile", on_delete=models.PROTECT, related_name="announcement_attachment")

    def __str__(self):
        return f"Attachment {self.file_id} on {self.announcement_id}"

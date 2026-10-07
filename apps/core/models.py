"""Cross-cutting models: base classes, append-only audit tables, stored files, capability catalog.

See DATABASE.md §2.8 and the "Append-only enforcement" section. The ORM guard in this module is
layer 1 of 4; the database triggers and privileges (core migration 0002) are layers 2 and 3; the
seal chain (AuditSeal) is layer 4.
"""

import uuid

from django.conf import settings
from django.db import connections, models, router


class AppendOnlyError(Exception):
    """Raised when code tries to modify or delete an append-only record."""


class TimeStampedModel(models.Model):
    """UUID primary key plus created/updated timestamps (UTC)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError(f"{self.model.__name__} rows are append-only and cannot be updated.")

    def delete(self):
        raise AppendOnlyError(f"{self.model.__name__} rows are append-only and cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise AppendOnlyError(f"{self.model.__name__} rows are append-only and cannot be updated.")

    def update_or_create(self, *args, **kwargs):
        raise AppendOnlyError(f"{self.model.__name__} rows are append-only; use create().")

    def _update(self, values):  # used internally by Model.save() for UPDATE statements
        raise AppendOnlyError(f"{self.model.__name__} rows are append-only and cannot be updated.")


class AppendOnlyModel(models.Model):
    """Rows can be inserted and read, never updated or deleted (ORM layer of the guard).

    ``seq`` is a monotonic sequence number used by the audit seal chain. On PostgreSQL it is
    assigned by a ``BEFORE INSERT`` trigger from a database sequence (a client cannot choose or
    forge it); on other engines it is assigned here (development/test only).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seq = models.BigIntegerField(unique=True, editable=False, default=0)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError(f"{type(self).__name__} rows are append-only and cannot be updated.")
        kwargs["force_insert"] = True
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        if connections[using].vendor != "postgresql":
            last = type(self).objects.using(using).aggregate(models.Max("seq"))["seq__max"] or 0
            self.seq = last + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError(f"{type(self).__name__} rows are append-only and cannot be deleted.")


class Outcome(models.TextChoices):
    SUCCESS = "SUCCESS", "Success"
    DENIED = "DENIED", "Denied"
    FAILED = "FAILED", "Failed"


class AuditLog(AppendOnlyModel):
    """Who did what to which object, when, from where, with before/after values (never secrets)."""

    timestamp = models.DateTimeField(auto_now_add=True)
    # PROTECT: users are deactivated, never deleted. Null = system/scheduled action.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_actions"
    )
    actor_identifier = models.CharField(max_length=150)
    actor_role = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.SUCCESS)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["timestamp"]),
            models.Index(fields=["actor", "timestamp"]),
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["action", "timestamp"]),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(outcome__in=Outcome.values), name="auditlog_outcome_valid"),
        ]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.actor_identifier} {self.action} {self.object_type}:{self.object_id}"


class SecurityEventType(models.TextChoices):
    LOGIN_SUCCESS = "LOGIN_SUCCESS", "Login success"
    LOGIN_FAILURE = "LOGIN_FAILURE", "Login failure"
    LOGOUT = "LOGOUT", "Logout"
    THROTTLED = "THROTTLED", "Throttled / locked out"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED", "Password reset requested"
    PASSWORD_RESET_COMPLETED = "PASSWORD_RESET_COMPLETED", "Password reset completed"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Password changed"
    MFA_CHALLENGE = "MFA_CHALLENGE", "MFA challenge presented"
    MFA_SUCCESS = "MFA_SUCCESS", "MFA success"
    MFA_FAILURE = "MFA_FAILURE", "MFA failure"
    MFA_ENROLLED = "MFA_ENROLLED", "MFA device enrolled"
    MFA_RESET = "MFA_RESET", "MFA reset"
    MFA_RECOVERY_USED = "MFA_RECOVERY_USED", "MFA recovery code used"
    PERMISSION_DENIED = "PERMISSION_DENIED", "Permission denied"
    RATE_LIMITED = "RATE_LIMITED", "Rate limited"
    UPLOAD_REJECTED = "UPLOAD_REJECTED", "Upload rejected"
    ROLE_CHANGED = "ROLE_CHANGED", "Role or capability changed"
    SESSION_EXPIRED = "SESSION_EXPIRED", "Session expired"


class SecurityEvent(AppendOnlyModel):
    timestamp = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=40, choices=SecurityEventType.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="security_events"
    )
    # HMAC of the attempted identifier: correlates attempts without storing what was typed.
    identifier_hash = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    path = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["event_type", "timestamp"]),
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["ip_address", "timestamp"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(event_type__in=SecurityEventType.values), name="securityevent_type_valid"
            ),
        ]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.event_type} {self.ip_address or ''}"


class SealStream(models.TextChoices):
    AUDIT = "AUDIT", "Audit log"
    SECURITY = "SECURITY", "Security events"
    REQUEST_HISTORY = "REQUEST_HISTORY", "Request status history"


class AuditSeal(AppendOnlyModel):
    """Hash over a contiguous ``seq`` range of an append-only stream, chained to the previous seal."""

    stream = models.CharField(max_length=20, choices=SealStream.choices)
    from_seq = models.BigIntegerField()
    to_seq = models.BigIntegerField()
    row_count = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    previous_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["stream", "from_seq"]
        constraints = [
            models.CheckConstraint(condition=models.Q(stream__in=SealStream.values), name="auditseal_stream_valid"),
            models.CheckConstraint(condition=models.Q(from_seq__lte=models.F("to_seq")), name="auditseal_range_ordered"),
            models.UniqueConstraint(fields=["stream", "from_seq"], name="auditseal_unique_start"),
        ]

    def __str__(self):
        return f"{self.stream} {self.from_seq}-{self.to_seq} {self.sha256[:12]}"


class FilePurpose(models.TextChoices):
    PROFILE_PHOTO = "PROFILE_PHOTO", "Profile photo"
    REQUEST_ATTACHMENT = "REQUEST_ATTACHMENT", "Request attachment"
    ANNOUNCEMENT_ATTACHMENT = "ANNOUNCEMENT_ATTACHMENT", "Announcement attachment"


class ScanStatus(models.TextChoices):
    NOT_SCANNED = "NOT_SCANNED", "Not scanned"
    CLEAN = "CLEAN", "Clean"
    INFECTED = "INFECTED", "Infected"


class StoredFile(models.Model):
    """A private uploaded file. Stored under a random server-generated name outside any web root."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    storage_name = models.CharField(max_length=100, unique=True)
    original_name = models.CharField(max_length=150)  # sanitised; display only, never used as a path
    content_type = models.CharField(max_length=100)  # sniffed from content, not client-supplied
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stored_files")
    purpose = models.CharField(max_length=30, choices=FilePurpose.choices)
    scan_status = models.CharField(max_length=15, choices=ScanStatus.choices, default=ScanStatus.NOT_SCANNED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(size__gt=0), name="storedfile_size_positive"),
            models.CheckConstraint(condition=models.Q(purpose__in=FilePurpose.values), name="storedfile_purpose_valid"),
            models.CheckConstraint(
                condition=models.Q(scan_status__in=ScanStatus.values), name="storedfile_scan_status_valid"
            ),
        ]

    def __str__(self):
        return f"{self.original_name} ({self.purpose})"


class PortalCapability(models.Model):
    """Permissions-only model: no table, no rows. ``Meta.permissions`` is the capability catalog.

    The catalog, role ceilings and default groups live in ``apps.core.capabilities`` (single source
    of truth); this list must match it (asserted by tests).
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("manage_students", "Create and edit student records and institutional fields"),
            ("manage_staff", "Create and edit staff profiles"),
            ("manage_academics", "Manage faculties, departments, programs, academic years and semesters"),
            ("manage_units", "Manage units, offerings, prerequisites and lecturer assignment"),
            ("record_grades", "Record grades for offerings the lecturer teaches"),
            ("manage_grades", "Record or amend any grade"),
            ("manage_timetable", "Create and edit timetable entries"),
            ("manage_hostels", "Manage hostels, rooms, beds, booking windows and allocations"),
            ("manage_request_config", "Manage request categories, routing and approval requirements"),
            ("review_requests", "Triage, assign and respond to requests in scope"),
            ("review_all_requests", "Review requests in every department"),
            ("approve_requests", "Approve or reject approval-type requests"),
            ("approve_transfers", "Approve or reject transfer requests"),
            ("execute_transfers", "Apply an approved transfer to the academic record"),
            ("manage_clubs", "Manage clubs, officers and memberships"),
            ("publish_announcements", "Publish announcements"),
            ("manage_user_accounts", "Activate, deactivate, reset password or MFA for student/staff accounts"),
            ("view_audit_logs", "Read audit logs and security events"),
            ("view_statistics", "View admin dashboard statistics"),
            ("manage_roles", "Change roles, groups and capabilities; manage admin accounts"),
            ("manage_system_settings", "Manage security and system configuration"),
            ("manage_backups", "Trigger and verify backups"),
        ]

    def __str__(self):
        return "Portal capability catalog"

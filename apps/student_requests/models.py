"""Student requests, transfer requests, messages, attachments and status history (DATABASE.md §2.6).

Statuses are a fixed vocabulary; the allowed transitions live in code (ARCHITECTURE.md §5.4, D8).
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import AppendOnlyModel, TimeStampedModel


class Priority(models.TextChoices):
    LOW = "LOW", "Low"
    NORMAL = "NORMAL", "Normal"
    HIGH = "HIGH", "High"
    URGENT = "URGENT", "Urgent"


class ApprovalCapability(models.TextChoices):
    APPROVE_REQUESTS = "approve_requests", "approve_requests"
    APPROVE_TRANSFERS = "approve_transfers", "approve_transfers"


class RequestCategory(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, max_length=2000)
    department = models.ForeignKey(
        "academics.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="request_categories"
    )
    default_priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    requires_approval = models.BooleanField(default=False)
    approval_capability = models.CharField(
        max_length=30, choices=ApprovalCapability.choices, default=ApprovalCapability.APPROVE_REQUESTS
    )
    allows_attachments = models.BooleanField(default=True)
    max_attachments = models.PositiveSmallIntegerField(default=3)
    is_transfer = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        verbose_name_plural = "request categories"
        ordering = ["sort_order", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(default_priority__in=Priority.values), name="category_priority_valid"),
            models.CheckConstraint(
                condition=Q(approval_capability__in=ApprovalCapability.values), name="category_approval_capability_valid"
            ),
            models.CheckConstraint(
                condition=Q(is_transfer=False) | Q(approval_capability=ApprovalCapability.APPROVE_TRANSFERS),
                name="category_transfer_needs_transfer_approval",
            ),
            models.CheckConstraint(
                condition=Q(is_transfer=False) | Q(requires_approval=True), name="category_transfer_requires_approval"
            ),
            models.CheckConstraint(
                condition=Q(max_attachments__gte=0, max_attachments__lte=10), name="category_max_attachments_range"
            ),
        ]

    def __str__(self):
        return self.name


class RequestStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    NEEDS_INFORMATION = "NEEDS_INFORMATION", "Needs information"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    RESOLVED = "RESOLVED", "Resolved"
    CLOSED = "CLOSED", "Closed"
    CANCELLED = "CANCELLED", "Cancelled"


TERMINAL_REQUEST_STATUSES = (RequestStatus.CLOSED, RequestStatus.CANCELLED)


class RequestNumberCounter(models.Model):
    """Per-year counter row, locked with SELECT ... FOR UPDATE to allocate REQ-YYYY-NNNNNN numbers."""

    year = models.PositiveSmallIntegerField(primary_key=True)
    last_value = models.BigIntegerField(default=0)

    def __str__(self):
        return f"{self.year}: {self.last_value}"


class StudentRequest(TimeStampedModel):
    # Human-friendly number shown to users; never used as proof of authorization.
    number = models.CharField(max_length=20, unique=True)
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.PROTECT, related_name="requests")
    category = models.ForeignKey(RequestCategory, on_delete=models.PROTECT, related_name="requests")
    subject = models.CharField(max_length=200)
    description = models.TextField(max_length=10_000)
    status = models.CharField(max_length=20, choices=RequestStatus.choices, default=RequestStatus.SUBMITTED)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    department = models.ForeignKey(
        "academics.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="requests"
    )
    assigned_to = models.ForeignKey(
        "accounts.StaffProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_requests"
    )
    resolution = models.TextField(blank=True, max_length=5000)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["department", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=RequestStatus.values), name="request_status_valid"),
            models.CheckConstraint(condition=Q(priority__in=Priority.values), name="request_priority_valid"),
        ]

    def __str__(self):
        return f"{self.number} {self.subject}"

    @property
    def is_open(self):
        return self.status not in TERMINAL_REQUEST_STATUSES


class Visibility(models.TextChoices):
    PUBLIC = "PUBLIC", "Visible to the student"
    INTERNAL = "INTERNAL", "Staff only"


class RequestMessage(models.Model):
    """Immutable thread message (no edit, no delete)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(StudentRequest, on_delete=models.PROTECT, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    body = models.TextField(max_length=5000)
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(visibility__in=Visibility.values), name="request_message_visibility_valid")
        ]

    def __str__(self):
        return f"Message {self.pk} on {self.request_id}"


class RequestAttachment(TimeStampedModel):
    request = models.ForeignKey(StudentRequest, on_delete=models.PROTECT, related_name="attachments")
    message = models.ForeignKey(
        RequestMessage, null=True, blank=True, on_delete=models.PROTECT, related_name="attachments"
    )
    file = models.OneToOneField("core.StoredFile", on_delete=models.PROTECT, related_name="request_attachment")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(visibility__in=Visibility.values), name="request_attachment_visibility_valid"
            )
        ]

    def __str__(self):
        return f"Attachment {self.file_id} on {self.request_id}"


class RequestStatusChange(AppendOnlyModel):
    request = models.ForeignKey(StudentRequest, on_delete=models.PROTECT, related_name="status_changes")
    from_status = models.CharField(max_length=20, choices=RequestStatus.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=RequestStatus.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    note = models.TextField(blank=True, max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "seq"]
        indexes = [models.Index(fields=["request", "created_at"])]

    def __str__(self):
        return f"{self.request_id}: {self.from_status} -> {self.to_status}"


class TransferType(models.TextChoices):
    PROGRAM = "PROGRAM", "Program transfer"
    DEPARTMENT = "DEPARTMENT", "Department transfer"
    CAMPUS = "CAMPUS", "Campus transfer"
    FACULTY = "FACULTY", "Faculty transfer"


class TransferRequest(TimeStampedModel):
    request = models.OneToOneField(StudentRequest, on_delete=models.PROTECT, related_name="transfer")
    transfer_type = models.CharField(max_length=12, choices=TransferType.choices)
    # Snapshots of the student's record at submission; execution re-checks them under lock.
    from_program = models.ForeignKey("academics.Program", on_delete=models.PROTECT, related_name="+")
    from_department = models.ForeignKey("academics.Department", on_delete=models.PROTECT, related_name="+")
    from_campus = models.CharField(max_length=100)
    to_program = models.ForeignKey(
        "academics.Program", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    to_department = models.ForeignKey(
        "academics.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    to_campus = models.CharField(max_length=100, blank=True)
    reason = models.TextField(max_length=5000)
    executed_at = models.DateTimeField(null=True, blank=True)
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(transfer_type__in=TransferType.values), name="transfer_type_valid"),
            # Exactly the target matching the transfer type is set. A PROGRAM or FACULTY transfer
            # names the destination program (a faculty move is a move into a program there).
            models.CheckConstraint(
                condition=(
                    Q(transfer_type__in=[TransferType.PROGRAM, TransferType.FACULTY], to_program__isnull=False,
                      to_department__isnull=True, to_campus="")
                    | Q(transfer_type=TransferType.DEPARTMENT, to_program__isnull=True,
                        to_department__isnull=False, to_campus="")
                    | (Q(transfer_type=TransferType.CAMPUS, to_program__isnull=True, to_department__isnull=True)
                       & ~Q(to_campus=""))
                ),
                name="transfer_target_matches_type",
            ),
            models.CheckConstraint(
                condition=Q(executed_at__isnull=True, executed_by__isnull=True)
                | Q(executed_at__isnull=False, executed_by__isnull=False),
                name="transfer_execution_fields_together",
            ),
        ]

    def __str__(self):
        return f"{self.transfer_type} transfer for {self.request_id}"

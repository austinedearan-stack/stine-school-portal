import uuid
from django.db import models
from django.conf import settings
from apps.core.utils import secure_filename

def request_attachment_path(instance, filename):
    safe_name = secure_filename(filename, prefix='req_att')
    return f"attachments/{instance.request.ticket_number}/{safe_name}"


class RequestCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    department = models.ForeignKey('academics.Department', null=True, blank=True, on_delete=models.SET_NULL, related_name='request_categories')
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'Request Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class StudentRequest(models.Model):
    STATUS_CHOICES = (
        ('SUBMITTED', 'Submitted'),
        ('UNDER_REVIEW', 'Under Review'),
        ('NEEDS_INFORMATION', 'Needs Information'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('RESOLVED', 'Resolved'),
        ('CLOSED', 'Closed'),
        ('CANCELLED', 'Cancelled'),
    )
    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('NORMAL', 'Normal'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket_number = models.CharField(max_length=30, unique=True, db_index=True)
    student = models.ForeignKey('accounts.StudentProfile', on_delete=models.CASCADE, related_name='requests')
    category = models.ForeignKey(RequestCategory, on_delete=models.PROTECT, related_name='requests')
    subject = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='SUBMITTED', db_index=True)
    priority = models.CharField(max_length=15, choices=PRIORITY_CHOICES, default='NORMAL')
    assigned_to = models.ForeignKey('accounts.StaffProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_requests')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['student', 'status']),
            models.Index(fields=['category', 'status']),
        ]

    def __str__(self):
        return f"[{self.ticket_number}] {self.subject} ({self.get_status_display()})"

    @classmethod
    def generate_ticket_number(cls):
        from django.utils import timezone
        import random
        year = timezone.now().year
        rand = random.randint(1000, 9999)
        unique_id = uuid.uuid4().hex[:4].upper()
        return f"REQ-{year}-{rand}{unique_id}"


class RequestMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(StudentRequest, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='ticket_messages')
    message = models.TextField()
    is_internal_note = models.BooleanField(default=False, help_text="Visible to staff/admin only")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Message by {self.sender.username} on {self.request.ticket_number}"


class RequestAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(StudentRequest, on_delete=models.CASCADE, related_name='attachments')
    message = models.ForeignKey(RequestMessage, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=request_attachment_path)
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Attachment {self.original_filename} on {self.request.ticket_number}"


class TransferRequest(models.Model):
    TRANSFER_TYPE_CHOICES = (
        ('PROGRAM', 'Academic Program Transfer'),
        ('DEPARTMENT', 'Department Transfer'),
        ('CAMPUS', 'Campus Relocation'),
        ('FACULTY', 'Faculty Transfer'),
    )
    STATUS_CHOICES = (
        ('SUBMITTED', 'Submitted'),
        ('UNDER_REVIEW', 'Under Department Review'),
        ('NEEDS_INFORMATION', 'Additional Information Required'),
        ('APPROVED', 'Approved by Academic Board'),
        ('REJECTED', 'Transfer Request Rejected'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket_number = models.CharField(max_length=30, unique=True, db_index=True)
    student = models.ForeignKey('accounts.StudentProfile', on_delete=models.CASCADE, related_name='transfer_requests')
    transfer_type = models.CharField(max_length=20, choices=TRANSFER_TYPE_CHOICES, default='PROGRAM')
    current_program = models.ForeignKey('academics.Program', on_delete=models.PROTECT, related_name='transfers_out')
    requested_program = models.ForeignKey('academics.Program', on_delete=models.PROTECT, related_name='transfers_in')
    reason = models.TextField()
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='SUBMITTED', db_index=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_transfers')
    review_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Transfer [{self.ticket_number}] - {self.student.student_id} to {self.requested_program.code}"

    @classmethod
    def generate_ticket_number(cls):
        from django.utils import timezone
        import random
        year = timezone.now().year
        rand = random.randint(1000, 9999)
        return f"TRF-{year}-{rand}"

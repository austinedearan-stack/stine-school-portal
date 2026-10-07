import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone

class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('REQUEST_STATUS', 'Request Status Update'),
        ('REQUEST_RESPONSE', 'New Response on Request'),
        ('HOSTEL_ALLOCATION', 'Hostel Bed Allocation'),
        ('UNIT_REGISTRATION', 'Unit Registration'),
        ('TIMETABLE_CHANGE', 'Timetable Change'),
        ('CLUB_DECISION', 'Club Membership Decision'),
        ('ANNOUNCEMENT', 'Campus Announcement'),
        ('SYSTEM', 'System Notification'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPES, default='SYSTEM')
    title = models.CharField(max_length=200)
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
        ]

    def __str__(self):
        return f"Notification to {self.recipient.username}: {self.title} ({'Read' if self.is_read else 'Unread'})"


class Announcement(models.Model):
    AUDIENCE_CHOICES = (
        ('ALL', 'All University Members'),
        ('STUDENTS', 'Students Only'),
        ('STAFF', 'Staff Only'),
        ('FACULTY', 'Specific Faculty'),
        ('DEPARTMENT', 'Specific Department'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    content = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='announcements')
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default='ALL')
    faculty = models.ForeignKey('academics.Faculty', null=True, blank=True, on_delete=models.SET_NULL, related_name='announcements')
    department = models.ForeignKey('academics.Department', null=True, blank=True, on_delete=models.SET_NULL, related_name='announcements')
    publish_date = models.DateTimeField(default=timezone.now)
    expiration_date = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-publish_date']

    def __str__(self):
        return f"{self.title} [{self.get_audience_display()}]"

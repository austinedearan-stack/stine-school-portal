import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models


class AuditLog(models.Model):
    """
    Immutable audit ledger for security-critical, administrative, and data-modifying events.
    Records actor, action, target entity, timestamp, IP address, user agent, and JSON diffs.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='audit_actions'
    )
    actor_identifier = models.CharField(max_length=150, help_text="Cached username/email or 'SYSTEM'")
    actor_role = models.CharField(max_length=50, blank=True)
    action = models.CharField(max_length=100, db_index=True)
    target_model = models.CharField(max_length=100, db_index=True)
    target_id = models.CharField(max_length=100, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    changes = models.JSONField(default=dict, help_text="Stores previous_state and new_state")
    status = models.CharField(max_length=30, default='SUCCESS')
    details = models.TextField(blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['actor', 'timestamp']),
            models.Index(fields=['target_model', 'target_id']),
            models.Index(fields=['action', 'timestamp']),
        ]

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {self.actor_identifier} - {self.action} on {self.target_model}:{self.target_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionDenied("Audit log entries are immutable and cannot be updated once created.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Audit log entries are permanent and cannot be deleted.")


class SecurityEventLog(models.Model):
    """
    Tracks security incidents, login attempts, MFA challenges, permission denials, and rate limits.
    """
    EVENT_TYPES = (
        ('LOGIN_SUCCESS', 'Login Success'),
        ('LOGIN_FAILURE', 'Login Failure'),
        ('LOGOUT', 'Logout'),
        ('LOCKOUT', 'Account Lockout Triggered'),
        ('PASSWORD_RESET', 'Password Reset Requested'),
        ('PASSWORD_CHANGED', 'Password Changed'),
        ('MFA_CHALLENGE', 'MFA Challenge Presented'),
        ('MFA_SUCCESS', 'MFA Verification Succeeded'),
        ('MFA_FAILURE', 'MFA Verification Failed'),
        ('PERMISSION_DENIED', 'Permission Denied / Authorization Failure'),
        ('IDOR_ATTEMPT', 'Potential Insecure Direct Object Reference Attempt'),
        ('RATE_LIMIT_EXCEEDED', 'Rate Limit Exceeded'),
        ('SUSPICIOUS_UPLOAD', 'Malicious or Disallowed File Upload Attempted'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES, db_index=True)
    username_attempted = models.CharField(max_length=150, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='security_events'
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    endpoint = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['event_type', 'timestamp']),
            models.Index(fields=['ip_address', 'timestamp']),
        ]

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {self.event_type} - {self.username_attempted or 'ANON'} ({self.ip_address})"

    def save(self, *args, **kwargs):
        if not self._state.adding and SecurityEventLog.objects.filter(pk=self.pk).exists():
            raise PermissionDenied("Security event log entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Security event log entries cannot be deleted.")

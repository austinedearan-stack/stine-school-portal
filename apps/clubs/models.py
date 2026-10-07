import uuid

from django.db import models


class Club(models.Model):
    CATEGORY_CHOICES = (
        ('ACADEMIC', 'Academic & Professional'),
        ('TECHNOLOGY', 'Technology & Innovation'),
        ('CULTURAL', 'Cultural & Arts'),
        ('SPORTS', 'Sports & Recreation'),
        ('COMMUNITY', 'Community Service & Charity'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=30, unique=True, db_index=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='ACADEMIC')
    description = models.TextField()
    advisor = models.ForeignKey('accounts.StaffProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='advised_clubs')
    meeting_info = models.CharField(max_length=200, blank=True, help_text="e.g. Every Wednesday 4 PM, Student Center Rm 204")
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def active_members_count(self):
        return self.memberships.filter(status='APPROVED').count()


class ClubMembership(models.Model):
    MEMBERSHIP_STATUS_CHOICES = (
        ('PENDING', 'Pending Approval'),
        ('APPROVED', 'Active Member'),
        ('REJECTED', 'Application Rejected'),
        ('LEFT', 'Former Member'),
    )
    ROLE_CHOICES = (
        ('MEMBER', 'General Member'),
        ('OFFICER', 'Committee Officer'),
        ('TREASURER', 'Treasurer'),
        ('SECRETARY', 'Secretary'),
        ('PRESIDENT', 'President'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('accounts.StudentProfile', on_delete=models.CASCADE, related_name='club_memberships')
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='memberships')
    status = models.CharField(max_length=20, choices=MEMBERSHIP_STATUS_CHOICES, default='PENDING', db_index=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='MEMBER')
    applied_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-applied_at']
        unique_together = ('student', 'club')

    def __str__(self):
        return f"{self.student.student_id} in {self.club.name} ({self.status})"


class ClubEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='events')
    title = models.CharField(max_length=200)
    description = models.TextField()
    event_date = models.DateTimeField()
    location = models.CharField(max_length=150)
    is_public = models.BooleanField(default=True)

    class Meta:
        ordering = ['event_date']

    def __str__(self):
        return f"{self.club.name}: {self.title} on {self.event_date.strftime('%Y-%m-%d %H:%M')}"

"""Clubs and societies (DATABASE.md §2.5, ARCHITECTURE.md D10)."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel


class ClubKind(models.TextChoices):
    CLUB = "CLUB", "Club"
    SOCIETY = "SOCIETY", "Society"


class Club(TimeStampedModel):
    kind = models.CharField(max_length=10, choices=ClubKind.choices, default=ClubKind.CLUB)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150, unique=True)
    category = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True, max_length=5000)
    advisor = models.ForeignKey(
        "accounts.StaffProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="advised_clubs"
    )
    meeting_info = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True, help_text="Club address, never a personal one.")
    requires_approval = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(condition=Q(kind__in=ClubKind.values), name="club_kind_valid")]

    def __str__(self):
        return self.name


class MembershipStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    LEFT = "LEFT", "Left"
    REMOVED = "REMOVED", "Removed"


class Position(models.TextChoices):
    MEMBER = "MEMBER", "Member"
    SECRETARY = "SECRETARY", "Secretary"
    TREASURER = "TREASURER", "Treasurer"
    VICE_CHAIR = "VICE_CHAIR", "Vice chair"
    CHAIR = "CHAIR", "Chair"


class ClubMembership(TimeStampedModel):
    club = models.ForeignKey(Club, on_delete=models.PROTECT, related_name="memberships")
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.PROTECT, related_name="club_memberships")
    status = models.CharField(max_length=10, choices=MembershipStatus.choices, default=MembershipStatus.PENDING)
    position = models.CharField(max_length=12, choices=Position.choices, default=Position.MEMBER)
    # Set only by a manage_clubs holder or the club's advisor; never by a student, never on one's own row.
    can_manage_members = models.BooleanField(default=False)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["club__name", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=MembershipStatus.values), name="membership_status_valid"),
            models.CheckConstraint(condition=Q(position__in=Position.values), name="membership_position_valid"),
            models.UniqueConstraint(
                fields=["club", "student"],
                condition=Q(status__in=[MembershipStatus.PENDING, MembershipStatus.APPROVED]),
                name="membership_one_open_per_club",
            ),
        ]

    def __str__(self):
        return f"{self.student_id} in {self.club_id} [{self.status}]"


class EventVisibility(models.TextChoices):
    PUBLIC = "PUBLIC", "Everyone"
    MEMBERS = "MEMBERS", "Members only"


class ClubEvent(TimeStampedModel):
    club = models.ForeignKey(Club, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=5000)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True)
    visibility = models.CharField(max_length=10, choices=EventVisibility.choices, default=EventVisibility.PUBLIC)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.CheckConstraint(condition=Q(starts_at__lt=F("ends_at")), name="club_event_times_ordered"),
            models.CheckConstraint(condition=Q(visibility__in=EventVisibility.values), name="club_event_visibility_valid"),
        ]

    def __str__(self):
        return f"{self.club} - {self.title}"

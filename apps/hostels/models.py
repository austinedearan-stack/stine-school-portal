"""Hostel hierarchy, booking windows, applications and allocations (DATABASE.md §2.4).

Bed occupancy is derived from allocations (ARCHITECTURE.md D12); the partial unique constraints
below are the database backstop for the booking races.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel


class GenderPolicy(models.TextChoices):
    MALE = "MALE", "Male only"
    FEMALE = "FEMALE", "Female only"
    MIXED = "MIXED", "Mixed"


class Hostel(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100, unique=True)
    campus = models.CharField(max_length=100, default="Main Campus")
    gender_policy = models.CharField(max_length=10, choices=GenderPolicy.choices, default=GenderPolicy.MIXED)
    rules = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=Q(gender_policy__in=GenderPolicy.values), name="hostel_gender_policy_valid")
        ]

    def __str__(self):
        return self.name


class HostelBuilding(TimeStampedModel):
    hostel = models.ForeignKey(Hostel, on_delete=models.PROTECT, related_name="buildings")
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["hostel__name", "name"]
        constraints = [models.UniqueConstraint(fields=["hostel", "name"], name="building_unique_per_hostel")]

    def __str__(self):
        return f"{self.hostel.name} / {self.name}"


class HostelFloor(TimeStampedModel):
    building = models.ForeignKey(HostelBuilding, on_delete=models.PROTECT, related_name="floors")
    level = models.SmallIntegerField()

    class Meta:
        ordering = ["building", "level"]
        constraints = [models.UniqueConstraint(fields=["building", "level"], name="floor_unique_per_building")]

    def __str__(self):
        return f"{self.building} floor {self.level}"


class RoomType(models.TextChoices):
    SINGLE = "SINGLE", "Single"
    DOUBLE = "DOUBLE", "Double"
    TRIPLE = "TRIPLE", "Triple"
    QUAD = "QUAD", "Quad"


class SpaceStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE", "Available"
    MAINTENANCE = "MAINTENANCE", "Under maintenance"
    CLOSED = "CLOSED", "Closed"


class Room(TimeStampedModel):
    floor = models.ForeignKey(HostelFloor, on_delete=models.PROTECT, related_name="rooms")
    number = models.CharField(max_length=20)
    room_type = models.CharField(max_length=10, choices=RoomType.choices, default=RoomType.DOUBLE)
    capacity = models.PositiveSmallIntegerField(default=2, validators=[MinValueValidator(1), MaxValueValidator(8)])
    status = models.CharField(max_length=12, choices=SpaceStatus.choices, default=SpaceStatus.AVAILABLE)
    fee_per_semester = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["floor", "number"]
        constraints = [
            models.UniqueConstraint(fields=["floor", "number"], name="room_unique_per_floor"),
            models.CheckConstraint(condition=Q(room_type__in=RoomType.values), name="room_type_valid"),
            models.CheckConstraint(condition=Q(capacity__gte=1, capacity__lte=8), name="room_capacity_range"),
            models.CheckConstraint(condition=Q(status__in=SpaceStatus.values), name="room_status_valid"),
            models.CheckConstraint(condition=Q(fee_per_semester__gte=0), name="room_fee_non_negative"),
        ]

    def __str__(self):
        return f"{self.floor.building} room {self.number}"


class Bed(TimeStampedModel):
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="beds")
    label = models.CharField(max_length=10)
    status = models.CharField(max_length=12, choices=SpaceStatus.choices, default=SpaceStatus.AVAILABLE)

    class Meta:
        ordering = ["room", "label"]
        constraints = [
            models.UniqueConstraint(fields=["room", "label"], name="bed_unique_per_room"),
            models.CheckConstraint(condition=Q(status__in=SpaceStatus.values), name="bed_status_valid"),
        ]

    def __str__(self):
        return f"{self.room} bed {self.label}"


class BookingMode(models.TextChoices):
    APPLICATION = "APPLICATION", "Application and allocation"
    DIRECT_BOOKING = "DIRECT_BOOKING", "Direct booking"


class HostelBookingWindow(TimeStampedModel):
    semester = models.ForeignKey("academics.Semester", on_delete=models.PROTECT, related_name="hostel_windows")
    mode = models.CharField(max_length=15, choices=BookingMode.choices, default=BookingMode.DIRECT_BOOKING)
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    acceptance_hours = models.PositiveSmallIntegerField(default=48)

    class Meta:
        ordering = ["-opens_at"]
        constraints = [
            models.CheckConstraint(condition=Q(mode__in=BookingMode.values), name="booking_window_mode_valid"),
            models.CheckConstraint(condition=Q(opens_at__lt=F("closes_at")), name="booking_window_ordered"),
        ]

    def __str__(self):
        return f"{self.semester} {self.mode} {self.opens_at:%Y-%m-%d}..{self.closes_at:%Y-%m-%d}"


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    WAITLISTED = "WAITLISTED", "Waitlisted"
    CANCELLED = "CANCELLED", "Cancelled"
    ALLOCATED = "ALLOCATED", "Allocated"


class HostelApplication(TimeStampedModel):
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.PROTECT, related_name="hostel_applications")
    semester = models.ForeignKey("academics.Semester", on_delete=models.PROTECT, related_name="hostel_applications")
    preferred_hostels = models.ManyToManyField(Hostel, through="HostelPreference", related_name="+")
    preferred_room_type = models.CharField(max_length=10, choices=RoomType.choices, blank=True)
    special_needs = models.TextField(blank=True, max_length=2000)  # visible to the student and manage_hostels only
    status = models.CharField(max_length=15, choices=ApplicationStatus.choices, default=ApplicationStatus.SUBMITTED)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, max_length=2000)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=ApplicationStatus.values), name="hostel_application_status_valid"),
            models.UniqueConstraint(
                fields=["student", "semester"],
                condition=~Q(status__in=[ApplicationStatus.CANCELLED, ApplicationStatus.REJECTED]),
                name="hostel_application_one_open_per_semester",
            ),
        ]

    def __str__(self):
        return f"Hostel application {self.student_id} {self.semester_id} [{self.status}]"


class HostelPreference(models.Model):
    application = models.ForeignKey(HostelApplication, on_delete=models.CASCADE, related_name="preferences")
    hostel = models.ForeignKey(Hostel, on_delete=models.PROTECT, related_name="+")
    rank = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])

    class Meta:
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(fields=["application", "rank"], name="hostel_preference_unique_rank"),
            models.UniqueConstraint(fields=["application", "hostel"], name="hostel_preference_unique_hostel"),
            models.CheckConstraint(condition=Q(rank__gte=1, rank__lte=3), name="hostel_preference_rank_range"),
        ]

    def __str__(self):
        return f"{self.application_id} #{self.rank}: {self.hostel_id}"


class AllocationStatus(models.TextChoices):
    PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE", "Offered, awaiting acceptance"
    ACTIVE = "ACTIVE", "Active"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Offer expired"
    CANCELLED = "CANCELLED", "Cancelled"
    TRANSFERRED = "TRANSFERRED", "Transferred"
    VACATED = "VACATED", "Vacated"


HOLDING_ALLOCATION_STATUSES = (AllocationStatus.PENDING_ACCEPTANCE, AllocationStatus.ACTIVE)


class HostelAllocation(TimeStampedModel):
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.PROTECT, related_name="hostel_allocations")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="allocations")
    semester = models.ForeignKey("academics.Semester", on_delete=models.PROTECT, related_name="hostel_allocations")
    application = models.ForeignKey(
        HostelApplication, null=True, blank=True, on_delete=models.PROTECT, related_name="allocations"
    )
    status = models.CharField(max_length=20, choices=AllocationStatus.choices, default=AllocationStatus.ACTIVE)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Null when the student booked the bed themselves.",
    )
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=AllocationStatus.values), name="hostel_allocation_status_valid"),
            models.UniqueConstraint(
                fields=["bed", "semester"],
                condition=Q(status__in=HOLDING_ALLOCATION_STATUSES),
                name="allocation_one_holder_per_bed",
            ),
            models.UniqueConstraint(
                fields=["student", "semester"],
                condition=Q(status__in=HOLDING_ALLOCATION_STATUSES),
                name="allocation_one_bed_per_student",
            ),
        ]

    def __str__(self):
        return f"{self.student_id} -> {self.bed_id} [{self.status}]"

import uuid
from django.db import models
from django.db.models import Q

class Hostel(models.Model):
    GENDER_POLICY_CHOICES = (
        ('MALE', 'Male Only'),
        ('FEMALE', 'Female Only'),
        ('MIXED', 'Mixed / Co-ed'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True, db_index=True)
    gender_policy = models.CharField(max_length=10, choices=GENDER_POLICY_CHOICES, default='MIXED')
    campus = models.CharField(max_length=100, default='Main Campus')
    warden_name = models.CharField(max_length=100, blank=True)
    warden_contact = models.CharField(max_length=50, blank=True)
    rules = models.TextField(blank=True, default="1. Quiet hours from 10 PM to 6 AM.\n2. No unauthorized visitors after 8 PM.\n3. Keep common facilities clean.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_gender_policy_display()})"


class HostelBuilding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name='buildings')
    name = models.CharField(max_length=100)  # e.g., "Block A"
    total_floors = models.PositiveSmallIntegerField(default=3)

    class Meta:
        ordering = ['hostel', 'name']
        unique_together = ('hostel', 'name')

    def __str__(self):
        return f"{self.hostel.name} - {self.name}"


class Room(models.Model):
    ROOM_TYPE_CHOICES = (
        ('SINGLE', 'Single Occupancy'),
        ('DOUBLE', 'Double Occupancy'),
        ('QUAD', 'Quad Occupancy (4 Beds)'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    building = models.ForeignKey(HostelBuilding, on_delete=models.CASCADE, related_name='rooms')
    room_number = models.CharField(max_length=20)  # e.g., "101"
    floor = models.PositiveSmallIntegerField(default=1)
    room_type = models.CharField(max_length=20, choices=ROOM_TYPE_CHOICES, default='DOUBLE')
    capacity = models.PositiveSmallIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['building', 'room_number']
        unique_together = ('building', 'room_number')

    def __str__(self):
        return f"{self.building.hostel.code} {self.building.name} - Room {self.room_number}"

    @property
    def available_beds_count(self):
        return self.beds.filter(status='AVAILABLE', is_occupied=False).count()


class Bed(models.Model):
    BED_STATUS_CHOICES = (
        ('AVAILABLE', 'Available'),
        ('OCCUPIED', 'Occupied'),
        ('MAINTENANCE', 'Under Maintenance'),
        ('RESERVED', 'Reserved'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='beds')
    bed_number = models.CharField(max_length=10)  # e.g., "A", "B"
    is_occupied = models.BooleanField(default=False, db_index=True)
    status = models.CharField(max_length=20, choices=BED_STATUS_CHOICES, default='AVAILABLE', db_index=True)

    class Meta:
        ordering = ['room', 'bed_number']
        unique_together = ('room', 'bed_number')

    def __str__(self):
        return f"{self.room} - Bed {self.bed_number} [{self.status}]"


class HostelApplication(models.Model):
    APPLICATION_STATUS_CHOICES = (
        ('SUBMITTED', 'Submitted'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('ALLOCATED', 'Allocated'),
        ('CANCELLED', 'Cancelled'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('accounts.StudentProfile', on_delete=models.CASCADE, related_name='hostel_applications')
    semester = models.ForeignKey('academics.Semester', on_delete=models.CASCADE, related_name='hostel_applications')
    preferred_hostel = models.ForeignKey(Hostel, null=True, blank=True, on_delete=models.SET_NULL)
    special_needs = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=APPLICATION_STATUS_CHOICES, default='SUBMITTED', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Hostel App - {self.student.student_id} [{self.status}]"


class HostelAllocation(models.Model):
    ALLOCATION_STATUS_CHOICES = (
        ('ACTIVE', 'Active Allocation'),
        ('TRANSFERRED', 'Transferred'),
        ('TERMINATED', 'Terminated / Vacated'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('accounts.StudentProfile', on_delete=models.CASCADE, related_name='hostel_allocations')
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name='allocations')
    semester = models.ForeignKey('academics.Semester', on_delete=models.PROTECT, related_name='hostel_allocations')
    status = models.CharField(max_length=20, choices=ALLOCATION_STATUS_CHOICES, default='ACTIVE', db_index=True)
    allocated_at = models.DateTimeField(auto_now_add=True)
    vacated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-allocated_at']
        constraints = [
            # Ensure a bed cannot have multiple ACTIVE allocations simultaneously
            models.UniqueConstraint(
                fields=['bed'],
                condition=models.Q(status='ACTIVE'),
                name='unique_active_bed_allocation'
            ),
            # Ensure a student cannot hold multiple ACTIVE hostel allocations in the same semester
            models.UniqueConstraint(
                fields=['student', 'semester'],
                condition=models.Q(status='ACTIVE'),
                name='unique_active_student_hostel_per_semester'
            )
        ]

    def __str__(self):
        return f"{self.student.student_id} -> {self.bed} [{self.status}]"

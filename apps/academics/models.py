import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Faculty(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = 'Faculties'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name='departments')
    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class Program(models.Model):
    DEGREE_CHOICES = (
        ('CERTIFICATE', 'Certificate'),
        ('DIPLOMA', 'Diploma'),
        ('UNDERGRADUATE', 'Bachelor Degree'),
        ('POSTGRADUATE', 'Master Degree / PhD'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='programs')
    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    degree_level = models.CharField(max_length=30, choices=DEGREE_CHOICES, default='UNDERGRADUATE')
    duration_years = models.PositiveSmallIntegerField(default=4)
    total_credit_required = models.PositiveIntegerField(default=120)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class AcademicYear(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=30, unique=True)  # e.g., "2026/2027"
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return self.name


class Semester(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='semesters')
    semester_number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])
    name = models.CharField(max_length=50)  # e.g., "Semester 1 (Fall)"
    start_date = models.DateField()
    end_date = models.DateField()
    registration_open = models.BooleanField(default=True)
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ['-academic_year__start_date', 'semester_number']
        unique_together = ('academic_year', 'semester_number')

    def __str__(self):
        return f"{self.academic_year.name} - {self.name}"


class Unit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True, db_index=True)  # e.g., "CS101"
    name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='units')
    credit_hours = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(6)])
    max_capacity = models.PositiveIntegerField(default=60)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    lecturer = models.ForeignKey('accounts.StaffProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_units')

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code}: {self.name} ({self.credit_hours} CH)"

    @property
    def current_enrolled_count(self):
        return self.registrations.filter(status='REGISTERED').count()

    @property
    def has_available_seats(self):
        return self.current_enrolled_count < self.max_capacity


class UnitPrerequisite(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='prerequisite_links')
    prerequisite = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='required_for_links')

    class Meta:
        unique_together = ('unit', 'prerequisite')

    def __str__(self):
        return f"{self.prerequisite.code} required for {self.unit.code}"


class UnitRegistration(models.Model):
    STATUS_CHOICES = (
        ('REGISTERED', 'Registered'),
        ('DROPPED', 'Dropped'),
        ('COMPLETED', 'Completed'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('accounts.StudentProfile', on_delete=models.CASCADE, related_name='unit_registrations')
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='registrations')
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name='unit_registrations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='REGISTERED', db_index=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    dropped_at = models.DateTimeField(null=True, blank=True)
    grade = models.CharField(max_length=5, blank=True)

    class Meta:
        ordering = ['-registered_at']
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'unit', 'semester'],
                condition=models.Q(status='REGISTERED'),
                name='unique_active_registration_per_student_unit_semester'
            )
        ]

    def __str__(self):
        return f"{self.student.student_id} - {self.unit.code} [{self.status}]"

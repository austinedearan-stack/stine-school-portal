"""Academic structure, unit catalogue, offerings and registrations (DATABASE.md §2.2)."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel


class Faculty(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "faculties"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Department(TimeStampedModel):
    faculty = models.ForeignKey(Faculty, on_delete=models.PROTECT, related_name="departments")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class AwardLevel(models.TextChoices):
    CERTIFICATE = "CERTIFICATE", "Certificate"
    DIPLOMA = "DIPLOMA", "Diploma"
    BACHELOR = "BACHELOR", "Bachelor's degree"
    MASTER = "MASTER", "Master's degree"
    DOCTORATE = "DOCTORATE", "Doctorate"


class Program(TimeStampedModel):
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="programs")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    award_level = models.CharField(max_length=20, choices=AwardLevel.choices, default=AwardLevel.BACHELOR)
    duration_years = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(8)])
    max_credits_per_semester = models.PositiveSmallIntegerField(
        default=24, validators=[MinValueValidator(1), MaxValueValidator(60)]
    )
    min_credits_per_semester = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(award_level__in=AwardLevel.values), name="program_award_level_valid"),
            models.CheckConstraint(
                condition=Q(duration_years__gte=1, duration_years__lte=8), name="program_duration_range"
            ),
            models.CheckConstraint(
                condition=Q(max_credits_per_semester__gte=1, max_credits_per_semester__lte=60),
                name="program_max_credits_range",
            ),
            models.CheckConstraint(
                condition=Q(min_credits_per_semester__gte=0)
                & Q(min_credits_per_semester__lte=F("max_credits_per_semester")),
                name="program_min_credits_le_max",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class AcademicYear(TimeStampedModel):
    name = models.CharField(max_length=30, unique=True)  # e.g. "2026/2027"
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(condition=Q(start_date__lt=F("end_date")), name="academic_year_dates_ordered"),
            models.UniqueConstraint(fields=["is_current"], condition=Q(is_current=True), name="one_current_academic_year"),
        ]

    def __str__(self):
        return self.name


class Semester(TimeStampedModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="semesters")
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])
    name = models.CharField(max_length=50)
    start_date = models.DateField()
    end_date = models.DateField()
    registration_opens_at = models.DateTimeField()
    registration_closes_at = models.DateTimeField()
    add_drop_deadline = models.DateTimeField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "number"], name="semester_unique_number_per_year"),
            models.CheckConstraint(condition=Q(number__gte=1, number__lte=3), name="semester_number_range"),
            models.CheckConstraint(condition=Q(start_date__lt=F("end_date")), name="semester_dates_ordered"),
            models.CheckConstraint(
                condition=Q(registration_opens_at__lt=F("registration_closes_at")),
                name="semester_registration_window_ordered",
            ),
            models.UniqueConstraint(fields=["is_current"], condition=Q(is_current=True), name="one_current_semester"),
        ]

    def __str__(self):
        return f"{self.academic_year.name} {self.name}"


class Unit(TimeStampedModel):
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="units")
    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    credit_hours = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(10)])
    level = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(8)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(credit_hours__gte=1, credit_hours__lte=10), name="unit_credit_range"),
            models.CheckConstraint(condition=Q(level__gte=1, level__lte=8), name="unit_level_range"),
        ]

    def __str__(self):
        return f"{self.code} {self.title}"


class UnitPrerequisite(TimeStampedModel):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="prerequisite_links")
    prerequisite = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="required_for_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["unit", "prerequisite"], name="prerequisite_unique_pair"),
            models.CheckConstraint(condition=~Q(unit=F("prerequisite")), name="prerequisite_not_self"),
        ]

    def __str__(self):
        return f"{self.prerequisite.code} required for {self.unit.code}"


class OfferingStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    OPEN = "OPEN", "Open"
    CLOSED = "CLOSED", "Closed"
    CANCELLED = "CANCELLED", "Cancelled"


class UnitOffering(TimeStampedModel):
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="offerings")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="offerings")
    section = models.CharField(max_length=10, default="A")
    lecturer = models.ForeignKey(
        "accounts.StaffProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="offerings"
    )
    capacity = models.PositiveIntegerField(default=60, validators=[MinValueValidator(1)])
    eligible_programs = models.ManyToManyField(Program, blank=True, related_name="restricted_offerings")
    min_year = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(8)])
    status = models.CharField(max_length=10, choices=OfferingStatus.choices, default=OfferingStatus.DRAFT)

    class Meta:
        ordering = ["unit__code", "section"]
        indexes = [models.Index(fields=["semester", "status"])]
        constraints = [
            models.UniqueConstraint(fields=["unit", "semester", "section"], name="offering_unique_section"),
            models.CheckConstraint(condition=Q(capacity__gte=1), name="offering_capacity_positive"),
            models.CheckConstraint(condition=Q(min_year__gte=1, min_year__lte=8), name="offering_min_year_range"),
            models.CheckConstraint(condition=Q(status__in=OfferingStatus.values), name="offering_status_valid"),
        ]

    def __str__(self):
        return f"{self.unit.code}/{self.section} ({self.semester})"


class RegistrationStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered"
    DROPPED = "DROPPED", "Dropped"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"


LIVE_REGISTRATION_STATUSES = (RegistrationStatus.REGISTERED, RegistrationStatus.COMPLETED)


class Grade(models.TextChoices):
    A = "A", "A"
    B = "B", "B"
    C = "C", "C"
    D = "D", "D"
    E = "E", "E (fail)"
    I = "I", "Incomplete"  # noqa: E741 - the grade letter


GRADE_POINTS = {"A": Decimal("4.00"), "B": Decimal("3.00"), "C": Decimal("2.00"), "D": Decimal("1.00"), "E": Decimal("0.00")}


class UnitRegistration(TimeStampedModel):
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.PROTECT, related_name="registrations")
    offering = models.ForeignKey(UnitOffering, on_delete=models.PROTECT, related_name="registrations")
    # Denormalised from the offering (set only by the registration service) for the uniqueness rule.
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="registrations")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="registrations")
    status = models.CharField(max_length=12, choices=RegistrationStatus.choices, default=RegistrationStatus.REGISTERED)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Set only for an administrative override; null = the student registered themselves.",
    )
    grade = models.CharField(max_length=2, choices=Grade.choices, blank=True)  # "" = not graded
    grade_points = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    graded_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    dropped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-registered_at"]
        indexes = [models.Index(fields=["offering", "status"]), models.Index(fields=["student", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "unit", "semester"],
                condition=Q(status__in=LIVE_REGISTRATION_STATUSES),
                name="registration_one_live_per_unit_semester",
            ),
            models.CheckConstraint(condition=Q(status__in=RegistrationStatus.values), name="registration_status_valid"),
            models.CheckConstraint(
                condition=Q(grade="") | Q(grade__in=Grade.values), name="registration_grade_valid"
            ),
            models.CheckConstraint(
                condition=Q(grade_points__isnull=True) | Q(grade_points__gte=0, grade_points__lte=4),
                name="registration_grade_points_range",
            ),
        ]

    def __str__(self):
        return f"{self.student_id} {self.unit_id} [{self.status}]"

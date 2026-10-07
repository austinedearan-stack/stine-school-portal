"""Venues, student groups and timetable entries (DATABASE.md §2.3).

Overlap protection has two layers: service validation (all engines, readable messages) and
PostgreSQL exclusion constraints created in migration 0002 (concurrent clashing inserts impossible).
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel


class Venue(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    campus = models.CharField(max_length=100, default="Main Campus")
    building = models.CharField(max_length=100, blank=True)
    capacity = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1)])
    venue_type = models.CharField(max_length=30, default="LECTURE_HALL")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.CheckConstraint(condition=Q(capacity__gte=1), name="venue_capacity_positive")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class StudentGroup(TimeStampedModel):
    program = models.ForeignKey("academics.Program", on_delete=models.PROTECT, related_name="student_groups")
    year_of_study = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(8)])
    label = models.CharField(max_length=20)  # e.g. "G1"

    class Meta:
        ordering = ["program__code", "year_of_study", "label"]
        constraints = [
            models.UniqueConstraint(fields=["program", "year_of_study", "label"], name="student_group_unique"),
            models.CheckConstraint(
                condition=Q(year_of_study__gte=1, year_of_study__lte=8), name="student_group_year_range"
            ),
        ]

    def __str__(self):
        return f"{self.program.code} Y{self.year_of_study} {self.label}"


class DayOfWeek(models.IntegerChoices):
    MONDAY = 1, "Monday"
    TUESDAY = 2, "Tuesday"
    WEDNESDAY = 3, "Wednesday"
    THURSDAY = 4, "Thursday"
    FRIDAY = 5, "Friday"
    SATURDAY = 6, "Saturday"
    SUNDAY = 7, "Sunday"


class ClassType(models.TextChoices):
    LECTURE = "LECTURE", "Lecture"
    TUTORIAL = "TUTORIAL", "Tutorial"
    LAB = "LAB", "Lab"
    WORKSHOP = "WORKSHOP", "Workshop"
    EXAM = "EXAM", "Exam"


class TimetableEntry(TimeStampedModel):
    offering = models.ForeignKey("academics.UnitOffering", on_delete=models.CASCADE, related_name="timetable_entries")
    # Denormalised from the offering; maintained by the timetable/offering services in one transaction.
    semester = models.ForeignKey("academics.Semester", on_delete=models.PROTECT, related_name="timetable_entries")
    lecturer = models.ForeignKey(
        "accounts.StaffProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="timetable_entries"
    )
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="timetable_entries")
    day_of_week = models.PositiveSmallIntegerField(choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    class_type = models.CharField(max_length=10, choices=ClassType.choices, default=ClassType.LECTURE)
    student_group = models.ForeignKey(
        StudentGroup, null=True, blank=True, on_delete=models.PROTECT, related_name="timetable_entries"
    )

    class Meta:
        ordering = ["day_of_week", "start_time"]
        verbose_name_plural = "timetable entries"
        indexes = [models.Index(fields=["venue", "day_of_week"]), models.Index(fields=["offering"])]
        constraints = [
            models.CheckConstraint(condition=Q(start_time__lt=F("end_time")), name="timetable_times_ordered"),
            models.CheckConstraint(condition=Q(day_of_week__gte=1, day_of_week__lte=7), name="timetable_day_range"),
            models.CheckConstraint(condition=Q(class_type__in=ClassType.values), name="timetable_class_type_valid"),
        ]

    def __str__(self):
        return f"{self.offering} {self.get_day_of_week_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

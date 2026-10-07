import uuid

from django.core.exceptions import ValidationError
from django.db import models


class Classroom(models.Model):
    ROOM_TYPE_CHOICES = (
        ('LECTURE_HALL', 'Lecture Hall'),
        ('LABORATORY', 'Science/Computer Laboratory'),
        ('SEMINAR_ROOM', 'Seminar Room'),
        ('TUTORIAL_ROOM', 'Tutorial Room'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)  # e.g., "LH-01"
    name = models.CharField(max_length=100)
    building = models.CharField(max_length=100)
    campus = models.CharField(max_length=100, default='Main Campus')
    capacity = models.PositiveIntegerField(default=50)
    room_type = models.CharField(max_length=30, choices=ROOM_TYPE_CHOICES, default='LECTURE_HALL')

    class Meta:
        ordering = ['building', 'code']

    def __str__(self):
        return f"{self.code} - {self.name} ({self.building})"


class TimetableEntry(models.Model):
    DAY_CHOICES = (
        ('MON', 'Monday'),
        ('TUE', 'Tuesday'),
        ('WED', 'Wednesday'),
        ('THU', 'Thursday'),
        ('FRI', 'Friday'),
        ('SAT', 'Saturday'),
        ('SUN', 'Sunday'),
    )
    CLASS_TYPE_CHOICES = (
        ('LECTURE', 'Lecture'),
        ('TUTORIAL', 'Tutorial'),
        ('LAB', 'Laboratory Session'),
        ('WORKSHOP', 'Workshop'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit = models.ForeignKey('academics.Unit', on_delete=models.CASCADE, related_name='timetable_entries')
    classroom = models.ForeignKey(Classroom, on_delete=models.PROTECT, related_name='timetable_slots')
    lecturer = models.ForeignKey('accounts.StaffProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='lectures')
    semester = models.ForeignKey('academics.Semester', on_delete=models.CASCADE, related_name='timetable_entries')
    day_of_week = models.CharField(max_length=3, choices=DAY_CHOICES, db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    class_type = models.CharField(max_length=20, choices=CLASS_TYPE_CHOICES, default='LECTURE')

    class Meta:
        ordering = ['day_of_week', 'start_time']
        indexes = [
            models.Index(fields=['semester', 'day_of_week', 'start_time']),
            models.Index(fields=['classroom', 'day_of_week']),
        ]

    def __str__(self):
        return f"{self.unit.code} [{self.day_of_week} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')}] in {self.classroom.code}"

    def clean(self):
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError("Class start time must be strictly earlier than end time.")

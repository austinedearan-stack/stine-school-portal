"""API serializers: explicit, minimal field lists (never ``__all__``); read-only."""

from rest_framework import serializers

from apps.academics.models import UnitOffering, UnitRegistration
from apps.accounts.models import StudentProfile, User
from apps.notifications.models import Notification
from apps.timetable.models import TimetableEntry


class StudentRecordSerializer(serializers.ModelSerializer):
    program = serializers.CharField(source="program.name")
    program_code = serializers.CharField(source="program.code")

    class Meta:
        model = StudentProfile
        fields = ["student_number", "program", "program_code", "year_of_study", "current_semester_number",
                  "academic_status", "campus", "phone", "personal_email"]
        read_only_fields = fields


class MeSerializer(serializers.ModelSerializer):
    student = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "role", "student"]
        read_only_fields = fields

    def get_student(self, user):
        profile = getattr(user, "student_profile", None) if user.role == "STUDENT" else None
        return StudentRecordSerializer(profile).data if profile else None


class OfferingSerializer(serializers.ModelSerializer):
    unit_code = serializers.CharField(source="unit.code")
    title = serializers.CharField(source="unit.title")
    credit_hours = serializers.IntegerField(source="unit.credit_hours")
    lecturer = serializers.SerializerMethodField()

    class Meta:
        model = UnitOffering
        fields = ["id", "unit_code", "title", "section", "credit_hours", "capacity", "status", "lecturer"]
        read_only_fields = fields

    def get_lecturer(self, offering):
        return offering.lecturer.user.full_name if offering.lecturer_id else None


class RegistrationSerializer(serializers.ModelSerializer):
    offering = OfferingSerializer()

    class Meta:
        model = UnitRegistration
        fields = ["id", "status", "grade", "registered_at", "offering"]
        read_only_fields = fields


class TimetableEntrySerializer(serializers.ModelSerializer):
    unit_code = serializers.CharField(source="offering.unit.code")
    venue = serializers.CharField(source="venue.code")
    day = serializers.CharField(source="get_day_of_week_display")

    class Meta:
        model = TimetableEntry
        fields = ["id", "unit_code", "day_of_week", "day", "start_time", "end_time", "class_type", "venue"]
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "read_at", "created_at"]
        read_only_fields = fields

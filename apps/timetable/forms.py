"""Timetable forms (explicit field allowlists)."""

from django import forms

from apps.academics.models import Department, OfferingStatus, UnitOffering
from apps.timetable.models import DayOfWeek, StudentGroup, TimetableEntry, Venue


class EntryForm(forms.ModelForm):
    class Meta:
        model = TimetableEntry
        fields = ["offering", "venue", "day_of_week", "start_time", "end_time", "class_type", "student_group"]
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}

    def __init__(self, *args, semester=None, **kwargs):
        super().__init__(*args, **kwargs)
        offerings = UnitOffering.objects.exclude(status=OfferingStatus.CANCELLED).select_related("unit", "semester")
        if semester is not None:
            offerings = offerings.filter(semester=semester)
        self.fields["offering"].queryset = offerings.order_by("unit__code", "section")
        self.fields["venue"].queryset = Venue.objects.filter(is_active=True)
        self.fields["student_group"].queryset = StudentGroup.objects.select_related("program")
        self.fields["student_group"].required = False


class VenueForm(forms.ModelForm):
    class Meta:
        model = Venue
        fields = ["code", "name", "campus", "building", "capacity", "venue_type", "is_active"]


class MasterFilterForm(forms.Form):
    department = forms.ModelChoiceField(queryset=Department.objects.filter(is_active=True), required=False,
                                        empty_label="All departments")
    venue = forms.ModelChoiceField(queryset=Venue.objects.filter(is_active=True), required=False,
                                   empty_label="All venues")
    day = forms.TypedChoiceField(choices=[("", "All days")] + list(DayOfWeek.choices), coerce=int,
                                 empty_value=None, required=False)
    group = forms.ModelChoiceField(queryset=StudentGroup.objects.select_related("program"), required=False,
                                   empty_label="All groups")

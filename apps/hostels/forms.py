"""Hostel forms (explicit fields only)."""

from django import forms

from apps.hostels.models import (
    ApplicationStatus,
    Hostel,
    HostelBookingWindow,
    RoomType,
    SpaceStatus,
)


class ApplicationForm(forms.Form):
    first_choice = forms.ModelChoiceField(queryset=Hostel.objects.filter(is_active=True))
    second_choice = forms.ModelChoiceField(queryset=Hostel.objects.filter(is_active=True), required=False)
    third_choice = forms.ModelChoiceField(queryset=Hostel.objects.filter(is_active=True), required=False)
    room_type = forms.ChoiceField(choices=[("", "No preference")] + list(RoomType.choices), required=False)
    special_needs = forms.CharField(
        required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional. Seen only by you and the accommodation office.")


class DecisionForm(forms.Form):
    status = forms.ChoiceField(choices=[(value, label) for value, label in ApplicationStatus.choices if value in (
        ApplicationStatus.UNDER_REVIEW, ApplicationStatus.APPROVED, ApplicationStatus.REJECTED,
        ApplicationStatus.WAITLISTED)])
    note = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))


class BedChoiceForm(forms.Form):
    bed = forms.ModelChoiceField(queryset=None, label="Bed")

    def __init__(self, *args, beds=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bed"].queryset = beds
        self.fields["bed"].label_from_instance = lambda bed: str(bed)


class OfferForm(BedChoiceForm):
    student_number = forms.CharField(max_length=30, required=False,
                                     help_text="Leave blank when offering against an application.")


class WindowForm(forms.ModelForm):
    class Meta:
        model = HostelBookingWindow
        fields = ["mode", "opens_at", "closes_at", "acceptance_hours"]
        widgets = {"opens_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
                   "closes_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class HostelForm(forms.ModelForm):
    class Meta:
        model = Hostel
        fields = ["code", "name", "campus", "gender_policy", "rules", "is_active"]


class RoomBatchForm(forms.Form):
    """Add a floor of identical rooms (with their beds) to a building in one step."""

    building = forms.CharField(max_length=100, initial="Block A")
    floor_level = forms.IntegerField(min_value=-5, max_value=50, initial=1)
    first_room_number = forms.IntegerField(min_value=1, max_value=99999, initial=101)
    rooms = forms.IntegerField(min_value=1, max_value=60, initial=10)
    room_type = forms.ChoiceField(choices=RoomType.choices, initial=RoomType.DOUBLE)
    beds_per_room = forms.IntegerField(min_value=1, max_value=8, initial=2)
    fee_per_semester = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, initial=0)


class SpaceStatusForm(forms.Form):
    status = forms.ChoiceField(choices=SpaceStatus.choices)

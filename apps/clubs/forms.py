"""Club forms (explicit fields only)."""

from django import forms

from apps.accounts.models import StaffProfile
from apps.clubs.models import Club, ClubEvent, ClubKind, Position


class DirectoryFilterForm(forms.Form):
    q = forms.CharField(label="Search", max_length=100, required=False,
                        widget=forms.TextInput(attrs={"type": "search", "placeholder": "Name or keyword"}))
    kind = forms.ChoiceField(choices=[("", "Clubs and societies")] + list(ClubKind.choices), required=False)
    category = forms.ChoiceField(choices=[("", "All categories")], required=False)

    def __init__(self, *args, categories=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [("", "All categories")] + [(c, c) for c in categories]


class ClubForm(forms.ModelForm):
    class Meta:
        model = Club
        fields = ["kind", "code", "name", "category", "description", "advisor", "meeting_info", "contact_email",
                  "requires_approval", "is_active"]

    def __init__(self, *args, can_set_advisor=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["advisor"].queryset = StaffProfile.objects.select_related("user").filter(user__is_active=True)
        if not can_set_advisor:
            del self.fields["advisor"]


class AppointForm(forms.Form):
    position = forms.ChoiceField(choices=Position.choices)
    can_manage_members = forms.BooleanField(required=False, label="Can approve members")


class EventForm(forms.ModelForm):
    class Meta:
        model = ClubEvent
        fields = ["title", "description", "starts_at", "ends_at", "location", "visibility"]
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
                   "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("starts_at") and cleaned.get("ends_at") and cleaned["starts_at"] >= cleaned["ends_at"]:
            raise forms.ValidationError("The event must end after it starts.")
        return cleaned

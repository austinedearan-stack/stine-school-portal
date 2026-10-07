"""Academics forms (explicit fields only)."""

from django import forms

from apps.academics.models import Department, Grade


class CatalogueFilterForm(forms.Form):
    q = forms.CharField(label="Search", max_length=100, required=False,
                        widget=forms.TextInput(attrs={"placeholder": "Code or title", "type": "search"}))
    department = forms.ModelChoiceField(queryset=Department.objects.filter(is_active=True), required=False,
                                        empty_label="All departments")
    level = forms.TypedChoiceField(choices=[("", "All levels")] + [(i, f"Level {i}") for i in range(1, 9)],
                                   coerce=int, empty_value=None, required=False)


class OverrideForm(forms.Form):
    student_number = forms.CharField(max_length=30, label="Student number")
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Required. Recorded in the audit log.")


class GradeForm(forms.Form):
    grade = forms.ChoiceField(choices=[("", "—")] + list(Grade.choices))

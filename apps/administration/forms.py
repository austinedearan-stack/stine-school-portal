"""Administration forms. Explicit field lists everywhere; role/group changes have their own forms."""

from django import forms
from django.contrib.auth.models import Group

from apps.academics.models import (
    AcademicYear,
    Department,
    Faculty,
    Program,
    Semester,
    Unit,
    UnitOffering,
)
from apps.accounts.models import StaffProfile, StudentProfile
from apps.administration.services import STAFF_RECORD_FIELDS, STUDENT_RECORD_FIELDS
from apps.core.capabilities import Role
from apps.core.models import SecurityEventType

DATE = forms.DateInput(attrs={"type": "date"})
DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"})


class UserSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100, label="Search",
                        widget=forms.TextInput(attrs={"type": "search", "placeholder": "Name, username, email, number"}))
    role = forms.ChoiceField(required=False, choices=[("", "All roles")] + list(Role.CHOICES))
    status = forms.ChoiceField(required=False, choices=[("", "Active and inactive"), ("active", "Active"),
                                                        ("inactive", "Inactive")])


class AccountFields(forms.Form):
    username = forms.CharField(max_length=50, help_text="The student or staff number used to sign in.")
    email = forms.EmailField(help_text="Institutional email; the set-password code is sent here.")
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)


class StudentRecordForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField()

    class Meta:
        model = StudentProfile
        fields = list(STUDENT_RECORD_FIELDS)
        widgets = {"admission_date": DATE, "expected_graduation": DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding:  # UUID pks exist before saving
            for field in ("first_name", "last_name", "email"):
                self.fields[field].initial = getattr(self.instance.user, field)


class NewStudentForm(AccountFields, StudentRecordForm):
    field_order = ["username", "email", "first_name", "last_name", *STUDENT_RECORD_FIELDS]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student_number"].help_text = "Usually the same as the username."


class StaffRecordForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField()

    class Meta:
        model = StaffProfile
        fields = list(STAFF_RECORD_FIELDS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding:  # UUID pks exist before saving
            for field in ("first_name", "last_name", "email"):
                self.fields[field].initial = getattr(self.instance.user, field)


class NewStaffForm(AccountFields, StaffRecordForm):
    role = forms.ChoiceField(choices=[(Role.STAFF, "Staff / Lecturer")])
    field_order = ["role", "username", "email", "first_name", "last_name", *STAFF_RECORD_FIELDS]

    def __init__(self, *args, allow_admin_roles=False, **kwargs):
        super().__init__(*args, **kwargs)
        if allow_admin_roles:
            self.fields["role"].choices = [(Role.STAFF, "Staff / Lecturer"), (Role.ADMIN, "Administrator"),
                                           (Role.SUPERADMIN, "Super administrator")]


class RoleForm(forms.Form):
    role = forms.ChoiceField(choices=Role.CHOICES)


class GroupsForm(forms.Form):
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.none(), required=False,
                                            widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, assignable=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = Group.objects.filter(pk__in=[g.pk for g in assignable]).order_by("name")


class AuditFilterForm(forms.Form):
    actor = forms.CharField(required=False, max_length=150, label="Actor (username)")
    action = forms.CharField(required=False, max_length=100, label="Action contains")
    object_id = forms.CharField(required=False, max_length=100, label="Object id")
    outcome = forms.ChoiceField(required=False, choices=[("", "Any outcome"), ("SUCCESS", "Success"),
                                                         ("DENIED", "Denied"), ("FAILED", "Failed")])
    since = forms.DateField(required=False, widget=DATE)
    until = forms.DateField(required=False, widget=DATE)


class SecurityFilterForm(forms.Form):
    event_type = forms.ChoiceField(required=False, choices=[("", "All events")] + list(SecurityEventType.choices))
    ip = forms.GenericIPAddressField(required=False, label="IP address")
    user = forms.CharField(required=False, max_length=150, label="User (username)")
    since = forms.DateField(required=False, widget=DATE)


# --- Academic setup: one registry drives list/create/edit pages ------------------------------------


def _model_form(model, fields, widgets=None):
    meta = type("Meta", (), {"model": model, "fields": fields, "widgets": widgets or {}})
    return type(f"{model.__name__}SetupForm", (forms.ModelForm,), {"Meta": meta})


SETUP_KINDS = {
    "faculties": {"model": Faculty, "label": "Faculties", "policy": "can_manage_academics",
                  "form": _model_form(Faculty, ["code", "name", "is_active"]), "columns": ["code", "name", "is_active"]},
    "departments": {"model": Department, "label": "Departments", "policy": "can_manage_academics",
                    "form": _model_form(Department, ["faculty", "code", "name", "is_active"]),
                    "columns": ["code", "name", "faculty", "is_active"]},
    "programs": {"model": Program, "label": "Programs", "policy": "can_manage_academics",
                 "form": _model_form(Program, ["department", "code", "name", "award_level", "duration_years",
                                               "min_credits_per_semester", "max_credits_per_semester", "is_active"]),
                 "columns": ["code", "name", "department", "award_level", "is_active"]},
    "years": {"model": AcademicYear, "label": "Academic years", "policy": "can_manage_academics",
              "form": _model_form(AcademicYear, ["name", "start_date", "end_date"],
                                  {"start_date": DATE, "end_date": DATE}),
              "columns": ["name", "start_date", "end_date", "is_current"]},
    "semesters": {"model": Semester, "label": "Semesters", "policy": "can_manage_academics",
                  "form": _model_form(Semester, ["academic_year", "number", "name", "start_date", "end_date",
                                                 "registration_opens_at", "registration_closes_at", "add_drop_deadline"],
                                      {"start_date": DATE, "end_date": DATE, "registration_opens_at": DATETIME,
                                       "registration_closes_at": DATETIME, "add_drop_deadline": DATETIME}),
                  "columns": ["name", "academic_year", "start_date", "end_date", "is_current"]},
    "units": {"model": Unit, "label": "Units", "policy": "can_manage_units",
              "form": _model_form(Unit, ["department", "code", "title", "description", "credit_hours", "level",
                                         "is_active"]),
              "columns": ["code", "title", "department", "credit_hours", "level", "is_active"]},
    "offerings": {"model": UnitOffering, "label": "Unit offerings", "policy": "can_manage_units",
                  "form": _model_form(UnitOffering, ["unit", "semester", "section", "capacity", "eligible_programs",
                                                     "min_year", "status"]),
                  "columns": ["unit", "semester", "section", "lecturer", "capacity", "status"]},
}


class PrerequisiteForm(forms.Form):
    prerequisite = forms.ModelChoiceField(queryset=Unit.objects.filter(is_active=True))


class LecturerForm(forms.Form):
    lecturer = forms.ModelChoiceField(queryset=StaffProfile.objects.filter(user__is_active=True).select_related("user"),
                                      required=False, empty_label="No lecturer")

"""Announcement form. Choices are narrowed to what the author may target; the policy re-checks on save."""

from django import forms
from django.utils import timezone

from apps.academics.models import Department, Faculty, OfferingStatus, Program, UnitOffering
from apps.accounts.models import User
from apps.clubs.models import Club
from apps.core.capabilities import Role
from apps.notifications.models import Announcement, Scope
from apps.student_requests.forms import MultipleFileField

SCOPE_FIELDS = {Scope.FACULTY: "faculty", Scope.DEPARTMENT: "department", Scope.PROGRAM: "program",
                Scope.OFFERING: "offering", Scope.CLUB: "club"}
STAFF_SCOPES = [Scope.DEPARTMENT, Scope.OFFERING, Scope.CLUB, Scope.INDIVIDUAL]


class AnnouncementForm(forms.ModelForm):
    recipient_numbers = forms.CharField(
        required=False, label="Recipients", widget=forms.Textarea(attrs={"rows": 2}),
        help_text="For 'Selected individuals': student or staff numbers / usernames, separated by commas.")
    attachments = MultipleFileField(required=False, help_text="PDF, JPEG or PNG; up to 5 MB each.")

    class Meta:
        model = Announcement
        fields = ["title", "body", "audience_roles", "scope", "faculty", "department", "program", "offering", "club",
                  "publish_at", "expires_at"]
        widgets = {"publish_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
                   "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
                   "body": forms.Textarea(attrs={"rows": 8})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["publish_at"].initial = timezone.now()
        offerings = UnitOffering.objects.exclude(status=OfferingStatus.CANCELLED).select_related("unit", "semester")
        if user is not None and user.role not in Role.ADMINS:
            staff = getattr(user, "staff_profile", None)
            self.fields["scope"].choices = [(value, label) for value, label in Scope.choices if value in STAFF_SCOPES]
            for name in ("faculty", "program"):
                del self.fields[name]
            self.fields["department"].queryset = Department.objects.filter(pk=getattr(staff, "department_id", None))
            self.fields["offering"].queryset = offerings.filter(lecturer=staff)
            self.fields["club"].queryset = Club.objects.filter(advisor=staff)
        else:
            self.fields["faculty"].queryset = Faculty.objects.filter(is_active=True)
            self.fields["program"].queryset = Program.objects.filter(is_active=True)
            self.fields["department"].queryset = Department.objects.filter(is_active=True)
            self.fields["offering"].queryset = offerings
            self.fields["club"].queryset = Club.objects.filter(is_active=True)
        for name in SCOPE_FIELDS.values():
            if name in self.fields:
                self.fields[name].required = False
        if self.instance and not self.instance._state.adding and self.instance.scope == Scope.INDIVIDUAL:
            self.fields["recipient_numbers"].initial = ", ".join(self.instance.recipients.values_list("username",
                                                                                                    flat=True))

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("scope")
        for s, name in SCOPE_FIELDS.items():
            if s != scope:
                cleaned[name] = None
                setattr(self.instance, name, None)
            elif not cleaned.get(name):
                self.add_error(name, "Choose the target for this scope.")
        self.recipients = None
        if scope == Scope.INDIVIDUAL:
            tokens = [t.strip() for t in (cleaned.get("recipient_numbers") or "").replace("\n", ",").split(",")
                      if t.strip()]
            if not tokens:
                self.add_error("recipient_numbers", "Name at least one recipient.")
            else:
                users = []
                for token in tokens[:200]:
                    user = (User.objects.filter(username__iexact=token).first() or
                            User.objects.filter(student_profile__student_number__iexact=token).first() or
                            User.objects.filter(staff_profile__staff_number__iexact=token).first())
                    if user is None:
                        self.add_error("recipient_numbers", f"Unknown recipient: {token}")
                    else:
                        users.append(user)
                self.recipients = users
        return cleaned

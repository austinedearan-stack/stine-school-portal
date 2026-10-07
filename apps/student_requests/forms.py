"""Request forms. Students never choose priority, status, department or assignee (audit Z-8)."""

from django import forms

from apps.academics.models import Department, Program
from apps.accounts.models import StaffProfile
from apps.student_requests.models import Priority, RequestCategory, RequestStatus, TransferType


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "application/pdf,image/jpeg,image/png"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, list | tuple):
            return [super(MultipleFileField, self).clean(item, initial) for item in data if item]
        return [super().clean(data, initial)] if data else []


class RequestForm(forms.Form):
    category = forms.ModelChoiceField(queryset=RequestCategory.objects.filter(is_active=True, is_transfer=False))
    subject = forms.CharField(max_length=200)
    description = forms.CharField(max_length=10_000, widget=forms.Textarea(attrs={"rows": 6}))
    attachments = MultipleFileField(required=False, help_text="PDF, JPEG or PNG; up to 5 MB each.")


class TransferRequestForm(forms.Form):
    transfer_type = forms.ChoiceField(choices=TransferType.choices)
    to_program = forms.ModelChoiceField(queryset=Program.objects.filter(is_active=True), required=False,
                                        help_text="For program or faculty transfers.")
    to_department = forms.ModelChoiceField(queryset=Department.objects.filter(is_active=True), required=False,
                                           help_text="For department transfers.")
    to_campus = forms.CharField(max_length=100, required=False, help_text="For campus transfers.")
    subject = forms.CharField(max_length=200)
    reason = forms.CharField(max_length=5000, widget=forms.Textarea(attrs={"rows": 6}))
    attachments = MultipleFileField(required=False, help_text="Supporting documents: PDF, JPEG or PNG; up to 5 MB each.")


class MessageForm(forms.Form):
    body = forms.CharField(label="Message", max_length=5000, widget=forms.Textarea(attrs={"rows": 4}))
    internal = forms.BooleanField(required=False, label="Internal note (staff only)")
    attachments = MultipleFileField(required=False)

    def __init__(self, *args, staff_view=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not staff_view:
            del self.fields["internal"]


class TransitionForm(forms.Form):
    target = forms.ChoiceField(label="New status")
    note = forms.CharField(max_length=5000, required=False, widget=forms.Textarea(attrs={"rows": 3}),
                           help_text="Required when asking for information, resolving, rejecting or closing.")

    def __init__(self, *args, targets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].choices = [(t, RequestStatus(t).label) for t in targets]


class AssignForm(forms.Form):
    assignee = forms.ModelChoiceField(queryset=StaffProfile.objects.none(), required=False,
                                      empty_label="Unassigned")

    def __init__(self, *args, candidates=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = candidates if candidates is not None else StaffProfile.objects.none()


class RoutingForm(forms.Form):
    priority = forms.ChoiceField(choices=Priority.choices)
    department = forms.ModelChoiceField(queryset=Department.objects.filter(is_active=True))


class ExecuteTransferForm(forms.Form):
    program = forms.ModelChoiceField(queryset=Program.objects.none(), required=False,
                                     help_text="Required for department transfers: the student's new program.")

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        if department is not None:
            self.fields["program"].queryset = Program.objects.filter(department=department, is_active=True)
        else:
            del self.fields["program"]


class QueueFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=50, label="Search",
                        widget=forms.TextInput(attrs={"type": "search", "placeholder": "Number, subject or student"}))
    status = forms.ChoiceField(required=False, choices=[("open", "All open"), ("", "Any status")] + list(
        RequestStatus.choices))
    category = forms.ModelChoiceField(queryset=RequestCategory.objects.all(), required=False, empty_label="All categories")
    mine = forms.BooleanField(required=False, label="Assigned to me")


class CategoryForm(forms.ModelForm):
    class Meta:
        model = RequestCategory
        fields = ["code", "name", "description", "department", "default_priority", "requires_approval",
                  "approval_capability", "allows_attachments", "max_attachments", "is_active", "sort_order"]

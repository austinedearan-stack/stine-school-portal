"""Account forms. Every ModelForm declares an explicit field allowlist (no __all__/exclude)."""

from django import forms
from django.contrib.auth.password_validation import password_validators_help_texts, validate_password
from django.core.exceptions import ValidationError

from apps.accounts.models import StaffProfile, StudentProfile


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label="Student/staff ID or email", max_length=254,
        widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}),
    )
    password = forms.CharField(
        label="Password", max_length=128, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class OneTimeCodeForm(forms.Form):
    code = forms.CharField(
        label="Authentication code", max_length=24,
        help_text="The 6-digit code from your authenticator app, or one of your recovery codes.",
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "text", "autofocus": True,
                                      "class": "code-input"}),
    )


class EnrollmentCodeForm(forms.Form):
    enrollment_code = forms.CharField(
        label="Enrollment code", max_length=24,
        help_text="The one-time enrollment code you received from IT support or the registrar.",
        widget=forms.TextInput(attrs={"autocomplete": "off", "autofocus": True}),
    )


class TotpConfirmForm(forms.Form):
    code = forms.CharField(
        label="6-digit code from the app", max_length=8,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True,
                                      "class": "code-input"}),
    )


class ReauthForm(forms.Form):
    """Re-authentication before sensitive account changes: password, plus a second factor if enrolled."""

    password = forms.CharField(label="Current password", max_length=128, strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    code = forms.CharField(label="Authentication or recovery code", max_length=24, required=False,
                           widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}))

    def __init__(self, user, *args, needs_code: bool = False, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["code"].required = needs_code
        if not needs_code:
            del self.fields["code"]


class NewPasswordMixin(forms.Form):
    new_password = forms.CharField(
        label="New password", max_length=128, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=" ".join(password_validators_help_texts()),
    )
    confirm_password = forms.CharField(label="Confirm new password", max_length=128, strip=False,
                                       widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    password_user = None  # the user the new password is validated against (similarity checks)

    def clean(self):
        cleaned = super().clean()
        new, confirm = cleaned.get("new_password"), cleaned.get("confirm_password")
        if new and confirm:
            if new != confirm:
                raise ValidationError("The two passwords do not match.")
            validate_password(new, user=self.password_user)
        return cleaned


class PasswordChangeForm(NewPasswordMixin):
    current_password = forms.CharField(label="Current password", max_length=128, strip=False,
                                       widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    field_order = ["current_password", "new_password", "confirm_password"]

    def __init__(self, user, *args, **kwargs):
        self.password_user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        value = self.cleaned_data.get("current_password")
        if not self.password_user.check_password(value):
            raise ValidationError("Your current password is incorrect.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password") and cleaned.get("new_password") == cleaned.get("current_password"):
            raise ValidationError("Choose a password different from your current one.")
        return cleaned


class PasswordResetRequestForm(forms.Form):
    identifier = forms.CharField(label="Student/staff ID or email", max_length=254,
                                 widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}))


class PasswordResetConfirmForm(NewPasswordMixin):
    identifier = forms.CharField(label="Student/staff ID or email", max_length=254,
                                 widget=forms.TextInput(attrs={"autocomplete": "username"}))
    code = forms.CharField(label="Reset code from the email", max_length=20,
                           widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}))
    field_order = ["identifier", "code", "new_password", "confirm_password"]


class StudentProfileEditForm(forms.ModelForm):
    """Only student-editable contact fields; institutional fields are never on this form."""

    class Meta:
        model = StudentProfile
        fields = [
            "phone",
            "personal_email",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
        ]


class StaffContactForm(forms.ModelForm):
    """Staff edit only their office and phone; title and department are institutional."""

    class Meta:
        model = StaffProfile
        fields = ["office", "phone"]


class PhotoUploadForm(forms.Form):
    photo = forms.FileField(
        label="Profile photo", help_text="JPEG or PNG, up to 2 MB. The image is re-processed before it is stored.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )

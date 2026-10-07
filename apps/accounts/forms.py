from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.accounts.models import StudentProfile
from apps.core.utils import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_IMAGE_MIMES,
    MAX_PROFILE_PHOTO_SIZE,
    validate_file_security,
)


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label="Student ID / Staff ID / Email",
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none',
            'placeholder': 'Enter your student ID or email',
            'autocomplete': 'username',
            'required': True,
        })
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password',
            'required': True,
        })
    )


class StudentProfileEditForm(forms.ModelForm):
    """
    Form strictly restricted to STUDENT-EDITABLE fields.
    Institutional fields (student_id, program, faculty, grades) are deliberately excluded.
    """
    class Meta:
        model = StudentProfile
        fields = [
            'phone',
            'profile_photo',
            'emergency_contact_name',
            'emergency_contact_phone',
            'emergency_contact_relationship',
        ]
        widgets = {
            'phone': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'placeholder': '+1 555-0199'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'placeholder': 'Jane Doe'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'placeholder': '+1 555-0198'}),
            'emergency_contact_relationship': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'placeholder': 'Parent / Guardian'}),
        }

    def clean_profile_photo(self):
        photo = self.cleaned_data.get('profile_photo')
        if photo and hasattr(photo, 'file'):
            validate_file_security(
                uploaded_file=photo,
                allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
                allowed_mimes=ALLOWED_IMAGE_MIMES,
                max_size_bytes=MAX_PROFILE_PHOTO_SIZE
            )
        return photo


class PasswordChangeCustomForm(forms.Form):
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'w-full px-4 py-2 border rounded-lg', 'placeholder': 'Current Password'})
    )
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'w-full px-4 py-2 border rounded-lg', 'placeholder': 'New Password (min 12 chars)'})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'w-full px-4 py-2 border rounded-lg', 'placeholder': 'Confirm New Password'})
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current_password = self.cleaned_data.get('current_password')
        if not self.user.check_password(current_password):
            raise ValidationError("Current password does not match our records.")
        return current_password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')

        if new_password and confirm_password:
            if new_password != confirm_password:
                raise ValidationError("New passwords do not match.")
            validate_password(new_password, user=self.user)
        return cleaned_data


class MFAVerifyForm(forms.Form):
    code = forms.CharField(
        label="Authentication Code / Backup Code",
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-3 text-center tracking-widest text-2xl border rounded-lg focus:ring-2 focus:ring-blue-500 font-mono',
            'placeholder': '123456 or ABCDE-12345',
            'autocomplete': 'one-time-code',
            'required': True,
        })
    )

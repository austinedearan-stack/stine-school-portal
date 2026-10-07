import base64
import io

import qrcode
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.forms import (
    LoginForm,
    MFAVerifyForm,
    PasswordChangeCustomForm,
    StudentProfileEditForm,
)
from apps.accounts.models import User
from apps.accounts.services import (
    authenticate_and_login,
    complete_user_login,
    generate_backup_codes,
    generate_totp_secret,
    get_totp_provisioning_uri,
    verify_backup_code,
    verify_totp,
)
from apps.core.permissions import ROLE_STUDENT, can_edit_student_profile
from apps.core.utils import get_client_ip, log_audit_event, log_security_event


def login_view(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        identifier = form.cleaned_data['identifier']
        password = form.cleaned_data['password']

        user, status = authenticate_and_login(request, identifier, password)

        if status == 'SUCCESS':
            messages.success(request, f"Welcome back, {user.full_name}!")
            return redirect('core:dashboard')
        elif status == 'MFA_REQUIRED':
            return redirect('accounts:mfa_verify')
        elif status == 'MFA_SETUP_REQUIRED':
            return redirect('accounts:mfa_setup')
        elif status == 'ACCOUNT_LOCKED':
            messages.error(request, "Account temporarily locked due to multiple failed login attempts. Please try again in 15 minutes.")
        else:
            # Generic error to prevent username/account enumeration
            messages.error(request, "Invalid credentials provided. Please check your identification and password.")

    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    if request.user.is_authenticated:
        user = request.user
        ip = get_client_ip(request)
        log_security_event('LOGOUT', user=user, ip_address=ip)
        log_audit_event(actor=user, action='AUTH_LOGOUT', target_model='User', target_id=str(user.id), ip_address=ip)
    logout(request)
    messages.info(request, "You have been securely logged out.")
    return redirect('accounts:login')


def mfa_verify_view(request):
    user_id = request.session.get('pre_mfa_user_id')
    if not user_id:
        return redirect('accounts:login')

    user = get_object_or_404(User, id=user_id)
    form = MFAVerifyForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        code = form.cleaned_data['code'].strip()

        # Try TOTP verification first
        if verify_totp(user, code):
            log_security_event('MFA_SUCCESS', user=user, ip_address=get_client_ip(request))
            return complete_user_login(request, user)
        # Try Backup Code verification
        elif verify_backup_code(user, code):
            log_security_event('MFA_SUCCESS', user=user, ip_address=get_client_ip(request), details={'method': 'backup_code'})
            messages.warning(request, "You logged in using a single-use backup code. Please generate new backup codes if running low.")
            return complete_user_login(request, user)
        else:
            log_security_event('MFA_FAILURE', user=user, ip_address=get_client_ip(request))
            messages.error(request, "Invalid authentication code. Please try again.")

    return render(request, 'accounts/mfa_verify.html', {'form': form, 'user_identifier': user.username})


def mfa_setup_view(request):
    user_id = request.session.get('pre_mfa_user_id')
    if user_id:
        user = get_object_or_404(User, id=user_id)
        if user.is_mfa_enabled:
            # Audit finding A-1 (containment): a password-only "pre-MFA" session must never be able to
            # enroll a NEW authenticator for an account that already has one -- that would bypass MFA.
            log_security_event(
                'MFA_FAILURE', user=user, ip_address=get_client_ip(request),
                endpoint=request.path, details={'reason': 'enrollment_blocked_device_exists'},
            )
            return redirect('accounts:mfa_verify')
    elif request.user.is_authenticated:
        if request.user.is_mfa_enabled:
            # Re-enrollment requires password + current code re-authentication (Phase 3). Until that
            # flow exists, replacing an existing device from a session is refused.
            raise PermissionDenied("MFA is already configured for this account.")
        user = request.user
    else:
        return redirect('accounts:login')

    # Session temporary secret or user secret
    secret = request.session.get('pending_mfa_secret')
    if not secret:
        secret = generate_totp_secret()
        request.session['pending_mfa_secret'] = secret

    # Generate QR Code image in base64
    provisioning_uri = get_totp_provisioning_uri(user, secret)
    qr = qrcode.make(provisioning_uri)
    buffer = io.BytesIO()
    qr.save(buffer, format='PNG')
    qr_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    form = MFAVerifyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        code = form.cleaned_data['code'].strip()
        temp_user = User(mfa_secret=secret)
        if verify_totp(temp_user, code):
            user.mfa_secret = secret
            user.is_mfa_enabled = True
            user.save(update_fields=['mfa_secret', 'is_mfa_enabled'])

            # Generate backup recovery codes
            backup_codes = generate_backup_codes(user)
            if 'pending_mfa_secret' in request.session:
                del request.session['pending_mfa_secret']

            # If came from login flow, complete login
            if not request.user.is_authenticated:
                complete_user_login(request, user)

            return render(request, 'accounts/mfa_backup_codes.html', {
                'backup_codes': backup_codes,
            })
        else:
            messages.error(request, "Invalid code entered. Please check your authenticator app time and try again.")

    return render(request, 'accounts/mfa_setup.html', {
        'form': form,
        'qr_b64': qr_b64,
        'secret': secret,
    })


@login_required
def profile_view(request):
    """
    Shows student or staff profile. Identity derived solely from request.user.
    """
    user = request.user
    student = getattr(user, 'student_profile', None)
    staff = getattr(user, 'staff_profile', None)

    return render(request, 'accounts/profile.html', {
        'profile_user': user,
        'student': student,
        'staff': staff,
    })


@login_required
def edit_student_profile_view(request):
    """
    Allows a student to edit only their own permitted fields.
    Institutional fields are strictly protected.
    """
    if request.user.role != ROLE_STUDENT:
        raise PermissionDenied("Only students have a student profile.")

    student = getattr(request.user, 'student_profile', None)
    if not student:
        raise PermissionDenied("Student profile does not exist.")

    if not can_edit_student_profile(request.user, student):
        raise PermissionDenied("You do not have permission to edit this profile.")

    form = StudentProfileEditForm(request.POST or None, request.FILES or None, instance=student)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_audit_event(
            actor=request.user,
            action='UPDATE_PROFILE',
            target_model='StudentProfile',
            target_id=str(student.id),
            changes={'updated_fields': list(form.changed_data)},
            ip_address=get_client_ip(request)
        )
        messages.success(request, "Profile details updated successfully.")
        return redirect('accounts:profile')

    return render(request, 'accounts/edit_profile.html', {'form': form, 'student': student})


@login_required
def password_change_view(request):
    form = PasswordChangeCustomForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        new_password = form.cleaned_data['new_password']
        request.user.set_password(new_password)
        request.user.last_password_change = timezone.now()
        request.user.save()
        update_session_auth_hash(request, request.user)

        log_security_event('PASSWORD_CHANGED', user=request.user, ip_address=get_client_ip(request))
        log_audit_event(
            actor=request.user,
            action='PASSWORD_CHANGE',
            target_model='User',
            target_id=str(request.user.id),
            ip_address=get_client_ip(request)
        )
        messages.success(request, "Your password has been securely updated.")
        return redirect('accounts:profile')

    return render(request, 'accounts/password_change.html', {'form': form})


def password_reset_request_view(request):
    """
    Password reset request with zero user-enumeration vulnerability.
    """
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        user = User.objects.filter(email__iexact=identifier).first() or \
               User.objects.filter(username__iexact=identifier).first()

        if user:
            log_security_event('PASSWORD_RESET', user=user, ip_address=get_client_ip(request))
            # In production, dispatch cryptographically signed reset token via email
        # Always return identical response
        messages.info(request, "If an account matches that identification, password reset instructions have been sent.")
        return redirect('accounts:login')

    return render(request, 'accounts/password_reset_request.html')

"""Account views (interim, Phase 2). Replaced by the Phase 3 authentication flow."""

import base64
import io

import qrcode
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.forms import LoginForm, MFAVerifyForm, PasswordChangeCustomForm, StudentProfileEditForm
from apps.accounts.models import User
from apps.accounts.services import (
    authenticate_and_login,
    check_new_secret,
    complete_user_login,
    confirmed_device,
    enroll_device,
    generate_totp_secret,
    get_totp_provisioning_uri,
    use_recovery_code,
    verify_totp,
)
from apps.core.audit import record_audit_event, record_security_event
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.core.models import SecurityEventType


def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user, status = authenticate_and_login(request, form.cleaned_data["identifier"], form.cleaned_data["password"])
        if status == "SUCCESS":
            return redirect("core:dashboard")
        if status == "MFA_REQUIRED":
            return redirect("accounts:mfa_verify")
        if status == "MFA_SETUP_REQUIRED":
            return redirect("accounts:mfa_setup")
        messages.error(request, "Invalid credentials.")
    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        ctx = RequestContext.from_request(request)
        record_security_event(SecurityEventType.LOGOUT, ctx=ctx, user=request.user)
        record_audit_event(request.user, "AUTH.LOGOUT", request.user, ctx=ctx)
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("accounts:login")


def _preauth_user(request):
    user_id = request.session.get("pre_mfa_user_id")
    return get_object_or_404(User, pk=user_id, is_active=True) if user_id else None


def mfa_verify_view(request):
    user = _preauth_user(request)
    if user is None:
        return redirect("accounts:login")
    device = confirmed_device(user)
    if device is None:
        return redirect("accounts:mfa_setup")
    form = MFAVerifyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ctx = RequestContext.from_request(request)
        code = form.cleaned_data["code"]
        if verify_totp(device, code):
            record_security_event(SecurityEventType.MFA_SUCCESS, ctx=ctx, user=user)
            complete_user_login(request, user)
            return redirect("core:dashboard")
        if use_recovery_code(user, code):
            record_security_event(SecurityEventType.MFA_RECOVERY_USED, ctx=ctx, user=user)
            complete_user_login(request, user)
            messages.warning(request, "You signed in with a recovery code. Each code works only once.")
            return redirect("core:dashboard")
        record_security_event(SecurityEventType.MFA_FAILURE, ctx=ctx, user=user)
        messages.error(request, "Invalid authentication code.")
    return render(request, "accounts/mfa_verify.html", {"form": form})


def mfa_setup_view(request):
    ctx = RequestContext.from_request(request)
    user = _preauth_user(request)
    if user is not None:
        if confirmed_device(user):
            # Audit finding A-1: a password-only pre-auth session must never enroll a NEW
            # authenticator for an account that already has one -- that would bypass MFA.
            record_security_event(
                SecurityEventType.MFA_FAILURE, ctx=ctx, user=user,
                details={"reason": "enrollment_blocked_device_exists"},
            )
            request.session.pop("pending_mfa_secret", None)
            return redirect("accounts:mfa_verify")
    elif request.user.is_authenticated:
        if confirmed_device(request.user):
            raise PermissionDenied("MFA is already configured for this account.")
        user = request.user
    else:
        return redirect("accounts:login")

    secret = request.session.get("pending_mfa_secret") or generate_totp_secret()
    request.session["pending_mfa_secret"] = secret
    form = MFAVerifyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if check_new_secret(secret, form.cleaned_data["code"]):
            recovery_codes = enroll_device(user, secret)
            request.session.pop("pending_mfa_secret", None)
            record_security_event(SecurityEventType.MFA_ENROLLED, ctx=ctx, user=user)
            record_audit_event(user, "MFA.ENROLLED", user, ctx=ctx)
            if not request.user.is_authenticated:
                complete_user_login(request, user)
            return render(request, "accounts/mfa_backup_codes.html", {"backup_codes": recovery_codes})
        messages.error(request, "Invalid code. Check your authenticator app's time and try again.")

    image = qrcode.make(get_totp_provisioning_uri(user, secret))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return render(
        request,
        "accounts/mfa_setup.html",
        {"form": form, "qr_b64": base64.b64encode(buffer.getvalue()).decode(), "secret": secret},
    )


@login_required
def profile_view(request):
    user = request.user
    return render(
        request,
        "accounts/profile.html",
        {
            "profile_user": user,
            "student": getattr(user, "student_profile", None),
            "staff": getattr(user, "staff_profile", None),
        },
    )


@login_required
def edit_student_profile_view(request):
    if request.user.role != Role.STUDENT or not hasattr(request.user, "student_profile"):
        raise PermissionDenied("Only students have a student profile.")
    student = request.user.student_profile
    form = StudentProfileEditForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        form.save()
        record_audit_event(
            request.user, "PROFILE.UPDATE", student, ctx=RequestContext.from_request(request),
            changes={"fields": sorted(form.changed_data)},
        )
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/edit_profile.html", {"form": form, "student": student})


@login_required
def password_change_view(request):
    form = PasswordChangeCustomForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["new_password"])
        request.user.save()
        update_session_auth_hash(request, request.user)
        ctx = RequestContext.from_request(request)
        record_security_event(SecurityEventType.PASSWORD_CHANGED, ctx=ctx, user=request.user)
        record_audit_event(request.user, "AUTH.PASSWORD_CHANGE", request.user, ctx=ctx)
        messages.success(request, "Your password has been updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/password_change.html", {"form": form})


def password_reset_request_view(request):
    """Placeholder until Phase 3 (audit A-4): identical response whether or not the account exists."""
    if request.method == "POST":
        messages.info(request, "If an account matches, password reset instructions have been sent.")
        return redirect("accounts:login")
    return render(request, "accounts/password_reset_request.html")

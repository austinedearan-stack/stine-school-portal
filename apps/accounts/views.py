"""Authentication and account security views (ARCHITECTURE.md §4)."""

from __future__ import annotations

import base64
import io
import time
from functools import wraps

import qrcode
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts import devices, mfa, passwords, sessions, throttle
from apps.accounts.forms import (
    EnrollmentCodeForm,
    LoginForm,
    OneTimeCodeForm,
    PasswordChangeForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    ReauthForm,
    StudentProfileEditForm,
    TotpConfirmForm,
)
from apps.accounts.models import UserSession
from apps.accounts.services import LoginOutcome, attempt_login, finish_login, resolve_identifier, safe_next_url
from apps.core.audit import record_audit_event, record_security_event
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.core.crypto import decrypt, encrypt
from apps.core.models import SecurityEventType
from apps.core.ratelimit import LimiterUnavailable
from apps.notifications.services import notify

GENERIC_LOGIN_ERROR = "Invalid credentials. Check your ID or email and password."
THROTTLED_MESSAGE = "Too many attempts. Please wait a few minutes and try again."
PENDING_SECRET_KEY = "pending_mfa_secret"  # noqa: S105 - session key name; the value is stored encrypted


def limiter_down(request) -> HttpResponse:
    return render(request, "errors/503.html", status=503)


def _after_login_response(request, next_url: str = "") -> HttpResponse:
    response = redirect(next_url or "core:dashboard")
    _attach_device_cookie(request, response)
    return response


def _attach_device_cookie(request, response) -> None:
    user = getattr(request, "_issue_device_cookie_for", None)
    if user is not None and devices.valid_device_for(request, user) is None:
        devices.issue(response, user)


def _qr_data_uri(user, secret: str) -> str:
    image = qrcode.make(mfa.provisioning_uri(user, secret))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _set_pending_secret(request, secret: str) -> None:
    request.session[PENDING_SECRET_KEY] = encrypt(secret)


def _pending_secret(request) -> str | None:
    token = request.session.get(PENDING_SECRET_KEY)
    if not token:
        return None
    try:
        return decrypt(token)
    except ValueError:
        request.session.pop(PENDING_SECRET_KEY, None)
        return None


# --- Login / logout -------------------------------------------------------------------------------


@never_cache
@sensitive_post_parameters("password")
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next"))
    form = LoginForm(request.POST or None)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            result = attempt_login(request, form.cleaned_data["identifier"], form.cleaned_data["password"], next_url)
        except LimiterUnavailable:
            return limiter_down(request)
        if result.outcome is LoginOutcome.SUCCESS:
            return _after_login_response(request, next_url)
        if result.outcome is LoginOutcome.MFA_VERIFY:
            return redirect("accounts:mfa_verify")
        if result.outcome is LoginOutcome.MFA_ENROLL:
            return redirect("accounts:mfa_enroll")
        if result.outcome is LoginOutcome.THROTTLED:
            form.add_error(None, THROTTLED_MESSAGE)
            status = 429
        else:
            form.add_error(None, GENERIC_LOGIN_ERROR)
    response = render(request, "accounts/login.html", {"form": form, "next": next_url}, status=status)
    if status == 429:
        response["Retry-After"] = "60"
    return response


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        ctx = RequestContext.from_request(request)
        record_security_event(SecurityEventType.LOGOUT, ctx=ctx, user=request.user)
        record_audit_event(request.user, "AUTH.LOGOUT", request.user, ctx=ctx)
        UserSession.objects.filter(session_key=request.session.session_key).delete()
    logout(request)  # flushes the session
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


# --- MFA at login ---------------------------------------------------------------------------------


def _mfa_failure(request, user, *, stage: str):
    ctx = RequestContext.from_request(request)
    throttle.mfa_failure(str(user.pk))
    attempts = sessions.preauth_data(request).get("attempts", 0) + 1
    sessions.update_preauth(request, attempts=attempts)
    record_security_event(SecurityEventType.MFA_FAILURE, ctx=ctx, user=user, details={"stage": stage})
    if attempts >= throttle.MFA_SESSION_LIMIT:
        sessions.clear_preauth(request)
        messages.error(request, "Too many incorrect codes. Please sign in again.")
        return redirect("accounts:login")
    return None


@never_cache
@require_http_methods(["GET", "POST"])
def mfa_verify_view(request):
    user = sessions.preauth_user(request)
    if user is None:
        return redirect("accounts:login")
    if mfa.confirmed_device(user) is None:
        return redirect("accounts:mfa_enroll")
    try:
        if throttle.mfa_user_blocked(str(user.pk)):
            sessions.clear_preauth(request)
            messages.error(request, THROTTLED_MESSAGE)
            return redirect("accounts:login")
    except LimiterUnavailable:
        return limiter_down(request)

    form = OneTimeCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        method = mfa.verify_second_factor(user, form.cleaned_data["code"])
        if method:
            next_url = sessions.preauth_data(request).get("next", "")
            if method == "recovery":
                record_security_event(SecurityEventType.MFA_RECOVERY_USED, ctx=RequestContext.from_request(request),
                                      user=user)
                messages.warning(request, f"You used a recovery code. {mfa.remaining_recovery_codes(user)} remain.")
            record_security_event(SecurityEventType.MFA_SUCCESS, ctx=RequestContext.from_request(request), user=user)
            finish_login(request, user, mfa_verified=True, method=f"mfa_{method}")
            return _after_login_response(request, next_url)
        try:
            response = _mfa_failure(request, user, stage="verify")
        except LimiterUnavailable:
            return limiter_down(request)
        if response is not None:
            return response
        form.add_error("code", "That code is not valid. Try the current code from your app.")
    return render(request, "accounts/mfa_verify.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def mfa_enroll_view(request):
    """First enrollment during login: needs a one-time enrollment code (issued out of band), then a
    TOTP confirmation. A pre-auth session can never replace an existing device (audit A-1)."""
    user = sessions.preauth_user(request)
    if user is None:
        return redirect("accounts:login")
    ctx = RequestContext.from_request(request)
    if mfa.confirmed_device(user):
        record_security_event(SecurityEventType.MFA_FAILURE, ctx=ctx, user=user,
                              details={"reason": "enrollment_blocked_device_exists"})
        request.session.pop(PENDING_SECRET_KEY, None)
        return redirect("accounts:mfa_verify")
    try:
        if throttle.mfa_user_blocked(str(user.pk)):
            sessions.clear_preauth(request)
            messages.error(request, THROTTLED_MESSAGE)
            return redirect("accounts:login")
    except LimiterUnavailable:
        return limiter_down(request)

    data = sessions.preauth_data(request)
    if not data.get("enroll_digest"):
        form = EnrollmentCodeForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            code = form.cleaned_data["enrollment_code"]
            if mfa.check_enrollment_code(user, code):
                sessions.update_preauth(request, enroll_digest=mfa.enrollment_code_digest(code))
                _set_pending_secret(request, mfa.new_secret())
                return redirect("accounts:mfa_enroll")
            try:
                response = _mfa_failure(request, user, stage="enrollment_code")
            except LimiterUnavailable:
                return limiter_down(request)
            if response is not None:
                return response
            form.add_error("enrollment_code", "That enrollment code is not valid or has expired.")
        return render(request, "accounts/mfa_enroll_code.html", {"form": form})

    secret = _pending_secret(request)
    if secret is None:
        secret = mfa.new_secret()
        _set_pending_secret(request, secret)
    form = TotpConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            recovery_codes = None
            if mfa.consume_enrollment_code(user, data["enroll_digest"]):
                recovery_codes = mfa.enroll(user, secret, form.cleaned_data["code"])
                if recovery_codes is None:
                    transaction.set_rollback(True)  # keep the enrollment code usable
        if recovery_codes is not None:
            request.session.pop(PENDING_SECRET_KEY, None)
            record_security_event(SecurityEventType.MFA_ENROLLED, ctx=ctx, user=user)
            record_audit_event(user, "MFA.ENROLLED", user, ctx=ctx)
            next_url = data.get("next", "")
            finish_login(request, user, mfa_verified=True, method="mfa_enrollment")
            response = render(request, "accounts/recovery_codes.html",
                              {"codes": recovery_codes, "continue_url": next_url or reverse("core:dashboard")})
            _attach_device_cookie(request, response)
            return response
        try:
            response = _mfa_failure(request, user, stage="enrollment_confirm")
        except LimiterUnavailable:
            return limiter_down(request)
        if response is not None:
            return response
        form.add_error("code", "That code did not match. Check the time on your phone and try again.")
    return render(request, "accounts/mfa_enroll_confirm.html",
                  {"form": form, "qr": _qr_data_uri(user, secret), "secret": secret})


# --- Logged-in security settings ------------------------------------------------------------------


def reauth_required(view):
    """Sensitive account changes need a fresh password (+ second factor) check within 5 minutes."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not sessions.recently_reauthenticated(request):
            return redirect(f"{reverse('accounts:reauth')}?{urlencode({'next': request.path})}")
        return view(request, *args, **kwargs)

    return login_required(wrapped)


@never_cache
@login_required
def security_view(request):
    user = request.user
    context = {
        "device": mfa.confirmed_device(user),
        "mfa_required": mfa.is_mfa_required(user),
        "recovery_remaining": mfa.remaining_recovery_codes(user),
        "trusted_devices": user.trusted_devices.filter(revoked_at__isnull=True).count(),
        "other_sessions": UserSession.objects.filter(user=user).exclude(session_key=request.session.session_key).count(),
    }
    return render(request, "accounts/security.html", context)


@never_cache
@sensitive_post_parameters("password", "code")
@login_required
@require_http_methods(["GET", "POST"])
def reauth_view(request):
    user = request.user
    needs_code = mfa.confirmed_device(user) is not None
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next")) or reverse(
        "accounts:security")
    form = ReauthForm(user, request.POST or None, needs_code=needs_code)
    if request.method == "POST" and form.is_valid():
        ctx = RequestContext.from_request(request)
        try:
            if throttle.mfa_user_blocked(str(user.pk)):
                form.add_error(None, THROTTLED_MESSAGE)
                return render(request, "accounts/reauth.html", {"form": form, "next": next_url}, status=429)
            ok = user.check_password(form.cleaned_data["password"])
            if ok and needs_code:
                ok = mfa.verify_second_factor(user, form.cleaned_data["code"]) is not None
            if ok:
                sessions.mark_reauthenticated(request)
                return redirect(next_url)
            throttle.mfa_failure(str(user.pk))
        except LimiterUnavailable:
            return limiter_down(request)
        record_security_event(SecurityEventType.MFA_FAILURE, ctx=ctx, user=user, details={"stage": "reauth"})
        form.add_error(None, "Those details are not correct.")
    return render(request, "accounts/reauth.html", {"form": form, "next": next_url})


@never_cache
@reauth_required
@require_http_methods(["GET", "POST"])
def mfa_setup_view(request):
    """Set up or replace the authenticator from a re-authenticated session."""
    user = request.user
    secret = _pending_secret(request)
    if secret is None or request.method == "GET" and "restart" in request.GET:
        secret = mfa.new_secret()
        _set_pending_secret(request, secret)
    form = TotpConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        replacing = mfa.confirmed_device(user) is not None
        recovery_codes = mfa.enroll(user, secret, form.cleaned_data["code"])
        if recovery_codes is not None:
            ctx = RequestContext.from_request(request)
            request.session.pop(PENDING_SECRET_KEY, None)
            request.session[sessions.MFA_VERIFIED_KEY] = int(time.time())  # this session just proved the factor
            record_security_event(SecurityEventType.MFA_ENROLLED, ctx=ctx, user=user,
                                  details={"replaced": replacing})
            record_audit_event(user, "MFA.REPLACED" if replacing else "MFA.ENROLLED", user, ctx=ctx)
            notify(user, "SECURITY", "Authenticator app changed",
                   "A new authenticator app was set up for your account. If this was not you, contact IT support.")
            return render(request, "accounts/recovery_codes.html",
                          {"codes": recovery_codes, "continue_url": reverse("accounts:security")})
        form.add_error("code", "That code did not match. Check the time on your phone and try again.")
    return render(request, "accounts/mfa_enroll_confirm.html",
                  {"form": form, "qr": _qr_data_uri(user, secret), "secret": secret, "logged_in": True})


@never_cache
@reauth_required
@require_POST
def recovery_codes_regenerate_view(request):
    user = request.user
    if mfa.confirmed_device(user) is None:
        return redirect("accounts:security")
    codes = mfa.issue_recovery_codes(user)
    record_audit_event(user, "MFA.RECOVERY_CODES_REGENERATED", user, ctx=RequestContext.from_request(request))
    return render(request, "accounts/recovery_codes.html", {"codes": codes, "continue_url": reverse("accounts:security")})


@never_cache
@reauth_required
@require_POST
def mfa_disable_view(request):
    user = request.user
    if mfa.is_mfa_required(user):
        messages.error(request, "Two-step verification is required for your account and cannot be turned off.")
        return redirect("accounts:security")
    mfa.remove_devices(user)
    request.session.pop(sessions.MFA_VERIFIED_KEY, None)
    ctx = RequestContext.from_request(request)
    record_security_event(SecurityEventType.MFA_RESET, ctx=ctx, user=user, details={"by": "self"})
    record_audit_event(user, "MFA.DISABLED", user, ctx=ctx)
    messages.success(request, "Two-step verification has been turned off.")
    return redirect("accounts:security")


@login_required
@require_POST
def sign_out_other_sessions_view(request):
    ended = sessions.end_all_sessions(request.user, except_key=request.session.session_key)
    devices.revoke_all(request.user)
    record_audit_event(request.user, "AUTH.OTHER_SESSIONS_ENDED", request.user, ctx=RequestContext.from_request(request),
                       changes={"sessions": ended})
    messages.success(request, f"Signed out of {ended} other session(s) and forgot remembered devices.")
    return redirect("accounts:security")


# --- Passwords ------------------------------------------------------------------------------------


@never_cache
@sensitive_post_parameters("current_password", "new_password", "confirm_password")
@login_required
@require_http_methods(["GET", "POST"])
def password_change_view(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        old_key = request.session.session_key
        passwords.set_new_password(request.user, form.cleaned_data["new_password"], RequestContext.from_request(request),
                                   reason="self_change")
        update_session_auth_hash(request, request.user)  # keeps this session; all others are now invalid
        UserSession.objects.filter(session_key=old_key).delete()
        UserSession.objects.update_or_create(session_key=request.session.session_key, defaults={"user": request.user})
        sessions.end_all_sessions(request.user, except_key=request.session.session_key)
        messages.success(request, "Your password has been changed. Other sessions were signed out.")
        return redirect("core:dashboard")
    return render(request, "accounts/password_change.html",
                  {"form": form, "forced": request.user.must_change_password})


@never_cache
@require_http_methods(["GET", "POST"])
def password_reset_request_view(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["identifier"]
        ctx = RequestContext.from_request(request)
        try:
            allowed = throttle.reset_request_allowed(identifier, ctx.ip)
        except LimiterUnavailable:
            return limiter_down(request)
        if allowed:
            passwords.request_reset(resolve_identifier(identifier), ctx)
        else:
            record_security_event(SecurityEventType.THROTTLED, ctx=ctx, identifier=identifier,
                                  details={"stage": "reset_request"})
        # Identical response whether or not the account exists or the request was throttled.
        messages.info(request, "If an account matches, we have emailed a reset code. It expires in 20 minutes.")
        return redirect("accounts:password_reset_confirm")
    return render(request, "accounts/password_reset_request.html", {"form": form})


@never_cache
@sensitive_post_parameters("code", "new_password", "confirm_password")
@require_http_methods(["GET", "POST"])
def password_reset_confirm_view(request):
    form = PasswordResetConfirmForm(request.POST or None)
    ctx = RequestContext.from_request(request)
    if request.method == "POST":
        try:
            if not throttle.reset_confirm_allowed(ctx.ip):
                form.is_valid()
                form.add_error(None, THROTTLED_MESSAGE)
                return render(request, "accounts/password_reset_confirm.html", {"form": form}, status=429)
        except LimiterUnavailable:
            return limiter_down(request)
        if form.is_valid():
            user = resolve_identifier(form.cleaned_data["identifier"])
            code = form.cleaned_data["code"]
            if passwords.code_is_valid(user, code):
                try:
                    if passwords.complete_reset(user, code, form.cleaned_data["new_password"], ctx):
                        sessions.end_all_sessions(user)
                        messages.success(request, "Your password has been reset. You can sign in now.")
                        return redirect("accounts:login")
                except ValidationError as exc:
                    form.add_error("new_password", exc)
                    return render(request, "accounts/password_reset_confirm.html", {"form": form})
            try:
                throttle.reset_confirm_failure(ctx.ip)
            except LimiterUnavailable:
                return limiter_down(request)
            form.add_error("code", "That code is not valid or has expired.")
    return render(request, "accounts/password_reset_confirm.html", {"form": form})


# --- Profile (expanded in Phase 4) ----------------------------------------------------------------


@login_required
def profile_view(request):
    user = request.user
    return render(request, "accounts/profile.html", {
        "profile_user": user,
        "student": getattr(user, "student_profile", None) if user.role == Role.STUDENT else None,
        "staff": getattr(user, "staff_profile", None),
    })


@login_required
@require_http_methods(["GET", "POST"])
def edit_student_profile_view(request):
    if request.user.role != Role.STUDENT or not hasattr(request.user, "student_profile"):
        raise PermissionDenied("Only students have a student profile.")
    student = request.user.student_profile
    form = StudentProfileEditForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        form.save()
        record_audit_event(request.user, "PROFILE.UPDATED", student, ctx=RequestContext.from_request(request),
                           changes={"fields": sorted(form.changed_data)})
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/edit_profile.html", {"form": form, "student": student})

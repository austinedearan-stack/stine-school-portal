"""Own profile, profile photo and the privacy-scoped student detail page (Phase 4)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts import profile_services
from apps.accounts.forms import PhotoUploadForm, StaffContactForm, StudentProfileEditForm
from apps.accounts.models import StudentProfile
from apps.core.authz import get_in_scope_or_404, is_allowed
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.core.files import protected_file_response
from apps.core.models import FilePurpose, StoredFile


def _student_of(user):
    return getattr(user, "student_profile", None) if user.role == Role.STUDENT else None


@login_required
def profile_view(request):
    user = request.user
    student = _student_of(user)
    return render(request, "accounts/profile.html", {
        "profile_user": user,
        "student": student,
        "staff": getattr(user, "staff_profile", None),
        "photo_form": PhotoUploadForm() if student else None,
    })


@login_required
@require_http_methods(["GET", "POST"])
def profile_edit_view(request):
    user = request.user
    ctx = RequestContext.from_request(request)
    student = _student_of(user)
    staff = getattr(user, "staff_profile", None)
    if student is not None:
        form = StudentProfileEditForm(request.POST or None, instance=student)
    elif staff is not None:
        form = StaffContactForm(request.POST or None, instance=staff)
    else:
        raise PermissionDenied("There is no editable profile for this account.")
    if request.method == "POST" and form.is_valid():
        # The service applies only allowlisted fields, whatever else was posted.
        if student is not None:
            profile_services.update_student_contacts(user, student, form.cleaned_data, ctx)
        else:
            profile_services.update_staff_contacts(user, staff, form.cleaned_data, ctx)
        messages.success(request, "Your contact details were saved.")
        return redirect("accounts:profile")
    return render(request, "accounts/edit_profile.html", {"form": form})


@login_required
@require_POST
def photo_upload_view(request):
    student = _student_of(request.user)
    if student is None:
        raise PermissionDenied("Only students have a profile photo.")
    form = PhotoUploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            profile_services.set_student_photo(request.user, student, form.cleaned_data["photo"],
                                               RequestContext.from_request(request))
            messages.success(request, "Your photo was updated.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
    else:
        messages.error(request, "Choose a JPEG or PNG image to upload.")
    return redirect("accounts:profile")


@login_required
@require_POST
def photo_remove_view(request):
    student = _student_of(request.user)
    if student is None:
        raise PermissionDenied("Only students have a profile photo.")
    profile_services.remove_student_photo(request.user, student, RequestContext.from_request(request))
    messages.success(request, "Your photo was removed.")
    return redirect("accounts:profile")


@login_required
def photo_view(request, file_id):
    stored = get_in_scope_or_404(
        request.user, "can_view_profile_photo", StoredFile.objects.filter(purpose=FilePurpose.PROFILE_PHOTO),
        ctx=RequestContext.from_request(request), pk=file_id,
    )
    return protected_file_response(stored, as_attachment=False)


@login_required
def student_detail_view(request, student_id):
    """Lecturers see name/ID/program of students in their own offerings; contacts need manage_students."""
    student = get_in_scope_or_404(
        request.user, "can_view_student",
        StudentProfile.objects.select_related("user", "program__department__faculty"),
        ctx=RequestContext.from_request(request), pk=student_id,
    )
    return render(request, "accounts/student_detail.html", {
        "student": student,
        "show_contacts": is_allowed(request.user, "can_view_student_contacts", student),
        "show_institutional": is_allowed(request.user, "can_manage_students") or student.user_id == request.user.pk,
    })

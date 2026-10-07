"""Admin panel (Phase 10). Every view is capability-gated; every change goes through an audited service."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.models import Semester, Unit, UnitOffering, UnitPrerequisite
from apps.accounts import admin_services, mfa
from apps.accounts.models import StaffProfile, StudentProfile, User
from apps.administration import services
from apps.administration.forms import (
    SETUP_KINDS,
    AuditFilterForm,
    GroupsForm,
    LecturerForm,
    NewStaffForm,
    NewStudentForm,
    PrerequisiteForm,
    RoleForm,
    SecurityFilterForm,
    StaffRecordForm,
    StudentRecordForm,
    UserSearchForm,
)
from apps.core.authz import capabilities_of, deny, get_in_scope_or_404, has_capability, is_allowed
from apps.core.authz.decorators import capability_required, policy_required
from apps.core.context import RequestContext
from apps.core.dashboard import admin_statistics
from apps.core.models import AuditLog, SecurityEvent, SecurityEventType
from apps.timetable.services import TimetableConflict, set_offering_lecturer

PAGE = 30


def _ctx(request):
    return RequestContext.from_request(request)


def _page(request, qs):
    query = request.GET.copy()
    query.pop("page", None)
    return Paginator(qs, PAGE).get_page(request.GET.get("page")), query.urlencode()


# --- Dashboard ------------------------------------------------------------------------------------


@capability_required("view_statistics")
def dashboard_view(request):
    from apps.academics.selectors import current_semester

    since = timezone.now() - timedelta(hours=24)
    security = SecurityEvent.objects.filter(timestamp__gte=since).values("event_type").annotate(n=Count("id"))
    return render(request, "administration/dashboard.html", {
        "stats": admin_statistics(current_semester()),
        "security": {row["event_type"]: row["n"] for row in security},
        "recent": AuditLog.objects.select_related("actor").order_by("-timestamp")[:15]
        if has_capability(request.user, "view_audit_logs") else None,
    })


# --- Accounts -------------------------------------------------------------------------------------


@policy_required("can_view_accounts")
def users_view(request):
    form = UserSearchForm(request.GET or None)
    qs = User.objects.select_related("student_profile", "staff_profile").order_by("username")
    if form.is_valid():
        q = form.cleaned_data["q"].strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) |
                           Q(last_name__icontains=q) | Q(student_profile__student_number__icontains=q) |
                           Q(staff_profile__staff_number__icontains=q))
        if form.cleaned_data["role"]:
            qs = qs.filter(role=form.cleaned_data["role"])
        if form.cleaned_data["status"]:
            qs = qs.filter(is_active=form.cleaned_data["status"] == "active")
    page, query = _page(request, qs)
    return render(request, "administration/users.html", {
        "form": form, "page": page, "query": query,
        "can_create_students": is_allowed(request.user, "can_manage_students"),
        "can_create_staff": is_allowed(request.user, "can_manage_staff") or is_allowed(request.user, "can_manage_roles"),
    })


def _user_or_404(request, user_id):
    return get_in_scope_or_404(request.user, "can_view_accounts",
                               User.objects.select_related("student_profile__program", "staff_profile__department"),
                               ctx=_ctx(request), pk=user_id)


@policy_required("can_view_accounts")
def user_detail_view(request, user_id):
    target = _user_or_404(request, user_id)
    actor = request.user
    can_roles = is_allowed(actor, "can_change_roles", target)
    return render(request, "administration/user_detail.html", {
        "target": target,
        "capabilities": capabilities_of(target),
        "groups": target.groups.order_by("name"),
        "device": mfa.confirmed_device(target),
        "mfa_required": mfa.is_mfa_required(target),
        "can_manage_account": is_allowed(actor, "can_manage_user_account", target),
        "can_roles": can_roles,
        "role_form": RoleForm(initial={"role": target.role}) if can_roles else None,
        "groups_form": GroupsForm(initial={"groups": target.groups.all()},
                                  assignable=admin_services.assignable_groups(target.role)) if can_roles else None,
        "can_edit_student": hasattr(target, "student_profile") and is_allowed(actor, "can_manage_students"),
        "can_edit_staff": hasattr(target, "staff_profile") and is_allowed(actor, "can_manage_staff"),
        "recent_security": SecurityEvent.objects.filter(user=target).order_by("-timestamp")[:10]
        if has_capability(actor, "view_audit_logs") else None,
    })


@policy_required("can_view_accounts")
@require_POST
def user_action_view(request, user_id, action):
    target = _user_or_404(request, user_id)
    ctx = _ctx(request)
    try:
        if action == "deactivate":
            admin_services.set_active(request.user, target, False, ctx)
            messages.success(request, "Account deactivated; all sessions ended.")
        elif action == "activate":
            admin_services.set_active(request.user, target, True, ctx)
            messages.success(request, "Account activated.")
        elif action == "reset-password":
            admin_services.force_password_reset(request.user, target, ctx)
            messages.success(request, "A reset code was emailed; the user must choose a new password.")
        elif action in ("reset-mfa", "enrollment-code"):
            code = (admin_services.reset_mfa if action == "reset-mfa" else admin_services.issue_enrollment_code)(
                request.user, target, ctx)
            # Shown once, to be handed over out of band after identity verification. Never emailed.
            return render(request, "administration/enrollment_code.html", {"target": target, "code": code})
        elif action == "role":
            form = RoleForm(request.POST)
            if form.is_valid():
                admin_services.change_role(request.user, target, form.cleaned_data["role"], ctx)
                messages.success(request, "Role changed; all groups were removed.")
        elif action == "groups":
            form = GroupsForm(request.POST, assignable=admin_services.assignable_groups(target.role))
            if form.is_valid():
                admin_services.set_groups(request.user, target, [g.name for g in form.cleaned_data["groups"]], ctx)
                messages.success(request, "Capability groups updated.")
            else:
                messages.error(request, "Choose groups allowed for this role.")
        else:
            raise Http404
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("administration:user_detail", user_id=target.pk)


@capability_required("manage_students")
@require_http_methods(["GET", "POST"])
def student_create_view(request):
    form = NewStudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            student = services.create_student(request.user, form.cleaned_data, _ctx(request))
            messages.success(request, f"Student {student.student_number} created; a set-password code was emailed.")
            return redirect("administration:user_detail", user_id=student.user_id)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "administration/form.html", {"form": form, "title": "New student"})


@capability_required("manage_staff", "manage_roles")
@require_http_methods(["GET", "POST"])
def staff_create_view(request):
    form = NewStaffForm(request.POST or None, allow_admin_roles=has_capability(request.user, "manage_roles"))
    if request.method == "POST" and form.is_valid():
        try:
            staff = services.create_staff(request.user, form.cleaned_data, form.cleaned_data["role"], _ctx(request))
            messages.success(request, f"Account {staff.user.username} created; a set-password code was emailed.")
            return redirect("administration:user_detail", user_id=staff.user_id)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "administration/form.html", {"form": form, "title": "New staff account"})


@capability_required("manage_students")
@require_http_methods(["GET", "POST"])
def student_edit_view(request, student_id):
    student = StudentProfile.objects.select_related("user").filter(pk=student_id).first()
    if student is None:
        raise Http404
    form = StudentRecordForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_student_record(request.user, student, form.cleaned_data, _ctx(request))
            messages.success(request, "Student record saved.")
            return redirect("administration:user_detail", user_id=student.user_id)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "administration/form.html", {"form": form, "title": f"Student record · {student}"})


@capability_required("manage_staff")
@require_http_methods(["GET", "POST"])
def staff_edit_view(request, staff_id):
    staff = StaffProfile.objects.select_related("user").filter(pk=staff_id).first()
    if staff is None:
        raise Http404
    form = StaffRecordForm(request.POST or None, instance=staff)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_staff_record(request.user, staff, form.cleaned_data, _ctx(request))
            messages.success(request, "Staff record saved.")
            return redirect("administration:user_detail", user_id=staff.user_id)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "administration/form.html", {"form": form, "title": f"Staff record · {staff}"})


# --- Academic setup -------------------------------------------------------------------------------


def _kind(request, kind):
    spec = SETUP_KINDS.get(kind)
    if spec is None:
        raise Http404
    if not is_allowed(request.user, spec["policy"]):
        deny(request.user, spec["policy"], ctx=_ctx(request))
    return spec


@capability_required("manage_academics", "manage_units")
def setup_index_view(request):
    kinds = [(key, spec) for key, spec in SETUP_KINDS.items() if is_allowed(request.user, spec["policy"])]
    return render(request, "administration/setup_index.html", {"kinds": kinds})


@capability_required("manage_academics", "manage_units")
def setup_list_view(request, kind):
    spec = _kind(request, kind)
    page, query = _page(request, spec["model"].objects.all())
    rows = [(obj, [getattr(obj, column) for column in spec["columns"]]) for obj in page.object_list]
    return render(request, "administration/setup_list.html", {
        "kind": kind, "spec": spec, "page": page, "query": query, "rows": rows,
        "headers": [spec["model"]._meta.get_field(c).verbose_name for c in spec["columns"]],
    })


@capability_required("manage_academics", "manage_units")
@require_http_methods(["GET", "POST"])
def setup_form_view(request, kind, object_id=None):
    spec = _kind(request, kind)
    obj = spec["model"].objects.filter(pk=object_id).first() if object_id else None
    if object_id and obj is None:
        raise Http404
    form = spec["form"](request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_setup_object(request.user, form.save(commit=False), spec["policy"], _ctx(request),
                                               changed_fields=form.changed_data)
            form.instance = saved
            form.save_m2m()
            messages.success(request, f"{spec['model']._meta.verbose_name.capitalize()} saved.")
            return redirect("administration:setup_edit", kind=kind, object_id=saved.pk)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
    extra = {}
    if obj is not None and kind == "units":
        extra = {"prerequisites": obj.prerequisite_links.select_related("prerequisite"),
                 "prereq_form": PrerequisiteForm()}
    if obj is not None and kind == "offerings":
        extra = {"lecturer_form": LecturerForm(initial={"lecturer": obj.lecturer})}
    if obj is not None and kind == "semesters":
        extra = {"can_set_current": not obj.is_current}
    return render(request, "administration/setup_form.html",
                  {"form": form, "kind": kind, "spec": spec, "obj": obj,
                   "verbose_name": spec["model"]._meta.verbose_name, **extra})


@capability_required("manage_units")
@require_POST
def prerequisite_add_view(request, unit_id):
    unit = Unit.objects.filter(pk=unit_id).first()
    form = PrerequisiteForm(request.POST)
    if unit is None:
        raise Http404
    if form.is_valid():
        try:
            services.add_prerequisite(request.user, unit, form.cleaned_data["prerequisite"], _ctx(request))
            messages.success(request, "Prerequisite added.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
    return redirect("administration:setup_edit", kind="units", object_id=unit.pk)


@capability_required("manage_units")
@require_POST
def prerequisite_remove_view(request, link_id):
    link = UnitPrerequisite.objects.select_related("unit", "prerequisite").filter(pk=link_id).first()
    if link is None:
        raise Http404
    services.remove_prerequisite(request.user, link, _ctx(request))
    messages.success(request, "Prerequisite removed.")
    return redirect("administration:setup_edit", kind="units", object_id=link.unit_id)


@capability_required("manage_units")
@require_POST
def offering_lecturer_view(request, offering_id):
    offering = UnitOffering.objects.filter(pk=offering_id).first()
    form = LecturerForm(request.POST)
    if offering is None:
        raise Http404
    if form.is_valid():
        try:
            set_offering_lecturer(request.user, offering, form.cleaned_data["lecturer"], _ctx(request))
            messages.success(request, "Lecturer updated (timetable entries updated too).")
        except TimetableConflict as exc:
            messages.error(request, exc.messages[0])
    return redirect("administration:setup_edit", kind="offerings", object_id=offering.pk)


@capability_required("manage_academics")
@require_POST
def set_current_semester_view(request, semester_id):
    semester = Semester.objects.filter(pk=semester_id).first()
    if semester is None:
        raise Http404
    services.set_current_semester(request.user, semester, _ctx(request))
    messages.success(request, f"{semester} is now the current semester.")
    return redirect("administration:setup_edit", kind="semesters", object_id=semester.pk)


# --- Audit and security viewers -------------------------------------------------------------------


def _day_bounds(day, *, end=False):
    return timezone.make_aware(datetime.combine(day, time.max if end else time.min))


@capability_required("view_audit_logs")
def audit_log_view(request):
    form = AuditFilterForm(request.GET or None)
    qs = AuditLog.objects.select_related("actor").order_by("-timestamp")
    if form.is_valid():
        data = form.cleaned_data
        if data["actor"]:
            qs = qs.filter(actor_identifier__iexact=data["actor"].strip())
        if data["action"]:
            qs = qs.filter(action__icontains=data["action"].strip())
        if data["object_id"]:
            qs = qs.filter(object_id=data["object_id"].strip())
        if data["outcome"]:
            qs = qs.filter(outcome=data["outcome"])
        if data["since"]:
            qs = qs.filter(timestamp__gte=_day_bounds(data["since"]))
        if data["until"]:
            qs = qs.filter(timestamp__lte=_day_bounds(data["until"], end=True))
    page, query = _page(request, qs)
    return render(request, "administration/audit_log.html", {"form": form, "page": page, "query": query})


@capability_required("view_audit_logs")
def security_events_view(request):
    form = SecurityFilterForm(request.GET or None)
    qs = SecurityEvent.objects.select_related("user").order_by("-timestamp")
    if form.is_valid():
        data = form.cleaned_data
        if data["event_type"]:
            qs = qs.filter(event_type=data["event_type"])
        if data["ip"]:
            qs = qs.filter(ip_address=data["ip"])
        if data["user"]:
            qs = qs.filter(user__username__iexact=data["user"].strip())
        if data["since"]:
            qs = qs.filter(timestamp__gte=_day_bounds(data["since"]))
    page, query = _page(request, qs)
    return render(request, "administration/security_events.html", {
        "form": form, "page": page, "query": query, "types": SecurityEventType})

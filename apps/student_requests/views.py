"""Student requests, review queue, transfers and request settings (Phase 9)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.models import StaffProfile
from apps.core.audit import record_audit_event
from apps.core.authz import get_in_scope_or_404, has_capability, is_allowed
from apps.core.authz.decorators import capability_required, policy_required, role_required
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.core.files import protected_file_response
from apps.student_requests import selectors, services
from apps.student_requests.forms import (
    AssignForm,
    CategoryForm,
    ExecuteTransferForm,
    MessageForm,
    QueueFilterForm,
    RequestForm,
    RoutingForm,
    TransferRequestForm,
    TransitionForm,
)
from apps.student_requests.models import (
    RequestAttachment,
    RequestCategory,
    RequestStatus,
    StudentRequest,
    TransferType,
)


def _student(request):
    return getattr(request.user, "student_profile", None) if request.user.role == Role.STUDENT else None


def _request_or_404(request, request_id):
    return get_in_scope_or_404(
        request.user, "can_view_request",
        StudentRequest.objects.select_related("category", "student__user", "student__program", "assigned_to__user",
                                              "department", "decided_by"),
        ctx=RequestContext.from_request(request), pk=request_id)


# --- Student --------------------------------------------------------------------------------------


@role_required(Role.STUDENT)
def my_requests_view(request):
    student = _student(request)
    if student is None:
        raise Http404
    return render(request, "requests/my_requests.html", {
        "page": Paginator(selectors.requests_for_student(student), 20).get_page(request.GET.get("page"))})


@role_required(Role.STUDENT)
@require_http_methods(["GET", "POST"])
def create_view(request):
    student = _student(request)
    if student is None:
        raise Http404
    form = RequestForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            req = services.submit(request.user, student, data["category"], data["subject"], data["description"],
                                  RequestContext.from_request(request), uploads=data["attachments"])
            messages.success(request, f"Request {req.number} submitted.")
            return redirect("requests:detail", request_id=req.pk)
        except services.RequestError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "requests/create.html", {"form": form, "title": "New request"})


@role_required(Role.STUDENT)
@require_http_methods(["GET", "POST"])
def transfer_create_view(request):
    student = _student(request)
    category = RequestCategory.objects.filter(is_transfer=True, is_active=True).first()
    if student is None or category is None:
        raise Http404
    form = TransferRequestForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            req = services.submit(
                request.user, student, category, data["subject"], data["reason"], RequestContext.from_request(request),
                uploads=data["attachments"], transfer={k: data.get(k) for k in (
                    "transfer_type", "to_program", "to_department", "to_campus", "reason")})
            messages.success(request, f"Transfer request {req.number} submitted.")
            return redirect("requests:detail", request_id=req.pk)
        except services.RequestError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "requests/create.html", {
        "form": form, "title": "Transfer request",
        "intro": f"You are currently in {student.program.name} ({student.program.department.name}), {student.campus}."})


# --- Detail and actions (owner and staff) ---------------------------------------------------------


@login_required
def detail_view(request, request_id):
    req = _request_or_404(request, request_id)
    user = request.user
    staff_view = req.student.user_id != user.pk
    targets = services.available_targets(user, req)
    can_review = is_allowed(user, "can_review_request", req)
    transfer = getattr(req, "transfer", None) if req.category.is_transfer else None
    context = {
        "req": req,
        "transfer": transfer,
        "staff_view": staff_view,
        "thread": selectors.thread(req, include_internal=staff_view),
        "attachments": selectors.attachments(req, include_internal=staff_view),
        "history": req.status_changes.select_related("actor").order_by("created_at", "seq"),
        "message_form": MessageForm(staff_view=staff_view) if is_allowed(user, "can_reply_request", req) else None,
        "transition_form": TransitionForm(targets=targets) if targets else None,
        "can_review": can_review,
        "assign_form": AssignForm(candidates=_assignees(req)) if can_review and req.is_open else None,
        "routing_form": RoutingForm(initial={"priority": req.priority, "department": req.department}) if can_review
        and req.is_open else None,
        "can_execute": bool(transfer) and req.status == RequestStatus.APPROVED and transfer.executed_at is None and
        is_allowed(user, "can_execute_transfer", req),
    }
    if context["can_execute"]:
        context["execute_form"] = ExecuteTransferForm(
            department=transfer.to_department if transfer.transfer_type == TransferType.DEPARTMENT else None)
    return render(request, "requests/detail.html", context)


def _assignees(req):
    candidates = StaffProfile.objects.filter(user__is_active=True).filter(
        Q(department_id=req.department_id) | Q(pk=req.assigned_to_id)).select_related("user")
    return StaffProfile.objects.filter(pk__in=[s.pk for s in candidates if
                                               has_capability(s.user, "review_requests")]).select_related("user")


@login_required
@require_POST
def message_view(request, request_id):
    req = _request_or_404(request, request_id)
    staff_view = req.student.user_id != request.user.pk
    form = MessageForm(request.POST, request.FILES, staff_view=staff_view)
    if form.is_valid():
        try:
            services.add_message(request.user, req, form.cleaned_data["body"], RequestContext.from_request(request),
                                 internal=form.cleaned_data.get("internal", False),
                                 uploads=form.cleaned_data["attachments"])
            messages.success(request, "Message posted.")
        except services.RequestError as exc:
            messages.error(request, exc.messages[0])
    else:
        messages.error(request, "Write a message (and check any attachments).")
    return redirect("requests:detail", request_id=req.pk)


@login_required
@require_POST
def transition_view(request, request_id):
    req = _request_or_404(request, request_id)
    target, note = request.POST.get("target", ""), request.POST.get("note", "")
    try:
        services.transition(request.user, req, target, note, RequestContext.from_request(request))
        messages.success(request, "Status updated.")
    except services.RequestError as exc:
        messages.error(request, exc.messages[0])
    return redirect("requests:detail", request_id=req.pk)


@login_required
@require_POST
def assign_view(request, request_id):
    req = _request_or_404(request, request_id)
    form = AssignForm(request.POST, candidates=_assignees(req))
    if form.is_valid():
        try:
            services.assign(request.user, req, form.cleaned_data["assignee"], RequestContext.from_request(request))
            messages.success(request, "Assignment saved.")
        except services.RequestError as exc:
            messages.error(request, exc.messages[0])
    else:
        messages.error(request, "Choose a reviewer from the list.")
    return redirect("requests:detail", request_id=req.pk)


@login_required
@require_POST
def routing_view(request, request_id):
    req = _request_or_404(request, request_id)
    form = RoutingForm(request.POST)
    if form.is_valid():
        services.set_priority_and_department(request.user, req, form.cleaned_data["priority"],
                                             form.cleaned_data["department"], RequestContext.from_request(request))
        messages.success(request, "Routing updated.")
    return redirect("requests:detail", request_id=req.pk)


@login_required
@require_POST
def execute_view(request, request_id):
    req = _request_or_404(request, request_id)
    transfer = getattr(req, "transfer", None)
    if transfer is None:
        raise Http404
    form = ExecuteTransferForm(request.POST, department=transfer.to_department
                               if transfer.transfer_type == TransferType.DEPARTMENT else None)
    if form.is_valid():
        try:
            services.execute_transfer(request.user, req, RequestContext.from_request(request),
                                      program=form.cleaned_data.get("program"))
            messages.success(request, "Transfer applied to the student's record.")
        except services.RequestError as exc:
            messages.error(request, exc.messages[0])
    return redirect("requests:detail", request_id=req.pk)


@login_required
def attachment_view(request, attachment_id):
    attachment = get_in_scope_or_404(
        request.user, "can_download_request_file",
        RequestAttachment.objects.select_related("request__student__user", "request__category", "file"),
        ctx=RequestContext.from_request(request), pk=attachment_id)
    return protected_file_response(attachment.file, as_attachment=True)


# --- Staff queue ----------------------------------------------------------------------------------


@policy_required("can_use_request_queue")
def queue_view(request):
    form = QueueFilterForm(request.GET or {"status": "open"})
    filters = form.cleaned_data if form.is_valid() else {"status": "open"}
    requests = selectors.queue(request.user, status=filters.get("status", "open"), category=filters.get("category"),
                               mine=filters.get("mine", False), query=(filters.get("q") or "").strip())
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "requests/queue.html", {
        "form": form, "page": Paginator(requests, 25).get_page(request.GET.get("page")), "query": query.urlencode()})


# --- Request settings -----------------------------------------------------------------------------


@capability_required("manage_request_config")
def categories_view(request):
    return render(request, "requests/categories.html",
                  {"categories": RequestCategory.objects.select_related("department").order_by("sort_order", "name")})


@capability_required("manage_request_config")
@require_http_methods(["GET", "POST"])
def category_form_view(request, category_id=None):
    ctx = RequestContext.from_request(request)
    category = get_in_scope_or_404(request.user, "can_manage_request_config", RequestCategory.objects.all(), ctx=ctx,
                                   pk=category_id) if category_id else None
    if category is not None and category.is_transfer:
        messages.info(request, "The transfer category's approval settings are fixed.")
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if category is not None and category.is_transfer:
            obj.is_transfer, obj.requires_approval, obj.approval_capability = True, True, "approve_transfers"
        obj.full_clean()
        obj.save()
        record_audit_event(request.user, "REQUEST_CATEGORY.SAVED", obj, ctx=ctx,
                           changes={"fields": sorted(form.changed_data)})
        messages.success(request, "Category saved.")
        return redirect("requests:categories")
    return render(request, "requests/category_form.html", {"form": form, "category": category})

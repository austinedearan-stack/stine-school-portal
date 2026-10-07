"""Student requests and transfers (ARCHITECTURE.md §5.4, D8, D9; DATABASE.md §2.6 and §3).

* Numbers REQ-YYYY-NNNNNN come from a per-year counter row locked with SELECT ... FOR UPDATE
  (no random numbers, no collisions - audit F-4). Numbers are labels, never proof of authorization.
* Priority and routing department come from the category, never from the student (audit Z-8).
* Every status change goes through ``transition``: row lock, state machine, actor check, history row,
  audit row and a notification to the student (audit Z-4).
* Approving a transfer never changes the academic record; ``execute_transfer`` is a separate,
  capability-gated, once-only operation (audit Z-5).
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academics.models import Program
from apps.accounts.models import StaffProfile, StudentProfile
from apps.core import files
from apps.core.audit import diff, record_audit_event, record_denial, record_security_event
from apps.core.authz import authorize, has_capability, is_allowed
from apps.core.context import SYSTEM as SYSTEM_CTX
from apps.core.context import RequestContext
from apps.core.models import FilePurpose, SecurityEventType
from apps.notifications.services import notify, notify_many
from apps.student_requests import workflow
from apps.student_requests.models import (
    RequestAttachment,
    RequestCategory,
    RequestMessage,
    RequestNumberCounter,
    RequestStatus,
    RequestStatusChange,
    StudentRequest,
    TransferRequest,
    TransferType,
    Visibility,
)


class RequestError(ValidationError):
    pass


# --- Numbering ------------------------------------------------------------------------------------


def allocate_number(year: int | None = None) -> str:
    """Must be called inside a transaction; the counter row lock serialises allocation per year."""
    year = year or timezone.now().year
    RequestNumberCounter.objects.get_or_create(year=year)
    counter = RequestNumberCounter.objects.select_for_update().get(year=year)
    counter.last_value += 1
    counter.save(update_fields=["last_value"])
    return f"REQ-{year}-{counter.last_value:06d}"


# --- Helpers --------------------------------------------------------------------------------------


def _reviewer_users(req) -> list:
    """Staff to tell about activity: the assignee, or every reviewer of the routing department."""
    if req.assigned_to_id:
        return [req.assigned_to.user]
    if req.department_id is None:
        return []
    candidates = StaffProfile.objects.filter(department_id=req.department_id, user__is_active=True).select_related("user")
    return [s.user for s in candidates if has_capability(s.user, "review_requests")]


def _history(req, source, target, actor, note) -> None:
    RequestStatusChange.objects.create(request=req, from_status=source, to_status=target,
                                       actor=actor if actor is not None and getattr(actor, "pk", None) else None,
                                       note=note[:5000])


def _attach(actor, req, uploads, ctx, *, message=None, visibility=Visibility.PUBLIC) -> list[RequestAttachment]:
    uploads = [u for u in (uploads or []) if u]
    if not uploads:
        return []
    category = req.category
    if not category.allows_attachments:
        raise RequestError("This category does not accept attachments.")
    existing = req.attachments.count()
    if existing + len(uploads) > category.max_attachments:
        raise RequestError(f"At most {category.max_attachments} attachments are allowed for this request.")
    cleaned = []
    for upload in uploads:
        try:
            cleaned.append(files.validate_upload(upload, FilePurpose.REQUEST_ATTACHMENT))
        except ValidationError as exc:
            record_security_event(SecurityEventType.UPLOAD_REJECTED, ctx=ctx, user=actor,
                                  details={"purpose": FilePurpose.REQUEST_ATTACHMENT, "reason": exc.messages[0]})
            raise RequestError(f"{files.sanitise_filename(upload.name)}: {exc.messages[0]}") from exc
    return [
        RequestAttachment.objects.create(
            request=req, message=message, uploaded_by=actor, visibility=visibility,
            file=files.store(clean, owner=actor, purpose=FilePurpose.REQUEST_ATTACHMENT))
        for clean in cleaned
    ]


# --- Submission -----------------------------------------------------------------------------------


@transaction.atomic
def submit(actor, student: StudentProfile, category: RequestCategory, subject: str, description: str,
           ctx: RequestContext, *, uploads=(), transfer: dict | None = None) -> StudentRequest:
    authorize(actor, "can_act_for_student", student, ctx=ctx)
    if not category.is_active:
        raise RequestError("This category is not available.")
    if category.is_transfer and not transfer:
        raise RequestError("Transfer requests need the transfer details.")
    if transfer and not category.is_transfer:
        raise RequestError("Transfer details are only accepted for the transfer category.")
    student = StudentProfile.objects.select_related("program__department").get(pk=student.pk)
    req = StudentRequest.objects.create(
        number=allocate_number(), student=student, category=category, subject=subject.strip()[:200],
        description=description.strip(), priority=category.default_priority,
        department=category.department or student.program.department,
    )
    if transfer:
        _create_transfer(req, student, transfer)
    _history(req, "", RequestStatus.SUBMITTED, actor, "Submitted")
    _attach(actor, req, uploads, ctx)
    record_audit_event(actor, "REQUEST.SUBMITTED", req, ctx=ctx,
                       changes={"after": {"number": req.number, "category": category.code}})
    notify(student.user, "REQUEST_STATUS", f"{req.number} received", f"Your request \"{req.subject}\" was submitted.",
           route_name="requests:detail", route_kwargs={"request_id": str(req.pk)})
    notify_many(_reviewer_users(req), "REQUEST_STATUS", f"New request {req.number}", req.subject,
                route_name="requests:detail", route_kwargs={"request_id": str(req.pk)})
    return req


def _create_transfer(req, student, data) -> TransferRequest:
    kind = data.get("transfer_type")
    program, department = student.program, student.program.department
    target = {"to_program": None, "to_department": None, "to_campus": ""}
    if kind in (TransferType.PROGRAM, TransferType.FACULTY):
        to_program = data.get("to_program")
        if to_program is None or to_program == program or not to_program.is_active:
            raise RequestError("Choose a different, active destination program.")
        if kind == TransferType.FACULTY and to_program.department.faculty_id == department.faculty_id:
            raise RequestError("A faculty transfer must be to a program in another faculty.")
        target["to_program"] = to_program
    elif kind == TransferType.DEPARTMENT:
        to_department = data.get("to_department")
        if to_department is None or to_department == department or not to_department.is_active:
            raise RequestError("Choose a different, active destination department.")
        target["to_department"] = to_department
    elif kind == TransferType.CAMPUS:
        campus = (data.get("to_campus") or "").strip()
        if not campus or campus.lower() == student.campus.lower():
            raise RequestError("Name a different campus.")
        target["to_campus"] = campus[:100]
    else:
        raise RequestError("Choose a transfer type.")
    return TransferRequest.objects.create(
        request=req, transfer_type=kind, from_program=program, from_department=department, from_campus=student.campus,
        reason=(data.get("reason") or "").strip() or req.description, **target)


# --- Messages -------------------------------------------------------------------------------------


@transaction.atomic
def add_message(actor, req: StudentRequest, body: str, ctx: RequestContext, *, internal: bool = False,
                uploads=()) -> RequestMessage:
    req = StudentRequest.objects.select_for_update(of=("self",)).select_related("category", "student__user").get(
        pk=req.pk)
    authorize(actor, "can_reply_request", req, ctx=ctx)
    is_owner = req.student.user_id == actor.pk
    if internal and is_owner:
        raise RequestError("Students cannot post internal notes.")
    body = (body or "").strip()
    if not body:
        raise RequestError("Write a message.")
    visibility = Visibility.INTERNAL if internal else Visibility.PUBLIC
    message = RequestMessage.objects.create(request=req, author=actor, body=body[:5000], visibility=visibility)
    _attach(actor, req, uploads, ctx, message=message, visibility=visibility)
    record_audit_event(actor, "REQUEST.MESSAGE", req, ctx=ctx, changes={"after": {"visibility": visibility}})
    if is_owner:
        if req.status == RequestStatus.NEEDS_INFORMATION:
            _apply_transition(None, req, RequestStatus.UNDER_REVIEW, "Student replied", ctx)
        notify_many(_reviewer_users(req), "REQUEST_RESPONSE", f"{req.number}: student replied", req.subject,
                    route_name="requests:detail", route_kwargs={"request_id": str(req.pk)})
    elif not internal:
        notify(req.student.user, "REQUEST_RESPONSE", f"{req.number}: new reply", "Staff replied to your request.",
               route_name="requests:detail", route_kwargs={"request_id": str(req.pk)})
    return message


# --- Status changes -------------------------------------------------------------------------------


def actor_kind(actor, req) -> list[str]:
    kinds = []
    if req.student.user_id == actor.pk:
        kinds.append(workflow.OWNER)
    if is_allowed(actor, "can_review_request", req):
        kinds.append(workflow.REVIEWER)
    if is_allowed(actor, "can_approve_request", req):
        kinds.append(workflow.APPROVER)
    return kinds


def available_targets(actor, req) -> list[str]:
    targets = []
    for kind in actor_kind(actor, req):
        targets += workflow.targets_from(req.status, kind, requires_approval=req.category.requires_approval)
    if req.status == RequestStatus.APPROVED and _transfer_pending(req):
        # An approved, unexecuted transfer can only be closed by formal withdrawal (approve_transfers).
        targets = [t for t in targets if t != RequestStatus.CLOSED]
        if is_allowed(actor, "can_approve_request", req):
            targets.append(RequestStatus.CLOSED)
    return sorted(set(targets), key=lambda s: RequestStatus.values.index(s))


def _transfer_pending(req) -> bool:
    return req.category.is_transfer and hasattr(req, "transfer") and req.transfer.executed_at is None


def _apply_transition(actor, req, target, note, ctx) -> None:
    source = req.status
    req.status = target
    fields = ["status", "updated_at"]
    if target in (RequestStatus.APPROVED, RequestStatus.REJECTED):
        req.decided_by, req.decided_at = actor, timezone.now()
        fields += ["decided_by", "decided_at"]
    if target == RequestStatus.RESOLVED:
        req.resolution = note
        fields.append("resolution")
    req.save(update_fields=fields)
    _history(req, source, target, actor, note)
    record_audit_event(actor, f"REQUEST.{target}", req, ctx=ctx,
                       changes={"before": {"status": source}, "after": {"status": target}})
    notify(req.student.user, "REQUEST_STATUS", f"{req.number}: {req.get_status_display()}",
           note[:300] if note and target != RequestStatus.UNDER_REVIEW else f"Your request is now {req.get_status_display().lower()}.",
           route_name="requests:detail", route_kwargs={"request_id": str(req.pk)})


@transaction.atomic
def transition(actor, req: StudentRequest, target: str, note: str, ctx: RequestContext) -> StudentRequest:
    req = StudentRequest.objects.select_for_update(of=("self",)).select_related(
        "category", "student__user", "assigned_to__user").get(pk=req.pk)
    authorize(actor, "can_view_request", req, ctx=ctx)
    step = workflow.find(req.status, target)
    if step is None or step.actor == workflow.SYSTEM or target not in available_targets(actor, req):
        # Recorded on the independent connection: this transaction is about to roll back.
        record_denial(actor, "REQUEST.TRANSITION", req, ctx=ctx, reason=f"{req.status}->{target}")
        label = RequestStatus(target).label if target in RequestStatus.values else target
        raise RequestError(f"You cannot move this request from {req.get_status_display()} to {label}.")
    note = (note or "").strip()
    closing_unexecuted_transfer = target == RequestStatus.CLOSED and _transfer_pending(req) and \
        req.status == RequestStatus.APPROVED
    if (step.note_required or closing_unexecuted_transfer) and not note:
        raise RequestError("A note is required for this change.")
    if target == RequestStatus.NEEDS_INFORMATION:
        RequestMessage.objects.create(request=req, author=actor, body=note[:5000], visibility=Visibility.PUBLIC)
    _apply_transition(actor, req, target, note, ctx)
    return req


@transaction.atomic
def assign(actor, req: StudentRequest, staff: StaffProfile | None, ctx: RequestContext) -> StudentRequest:
    req = StudentRequest.objects.select_for_update(of=("self",)).select_related("category", "student__user").get(
        pk=req.pk)
    authorize(actor, "can_review_request", req, ctx=ctx)
    if not req.is_open:
        raise RequestError("Closed requests cannot be reassigned.")
    if staff is not None:
        if not staff.user.is_active or not has_capability(staff.user, "review_requests"):
            raise RequestError("Requests can only be assigned to active reviewers.")
        if not has_capability(staff.user, "review_all_requests") and staff.department_id != req.department_id:
            raise RequestError("That reviewer does not cover this request's department.")
    before = {"assigned_to": req.assigned_to.user.username if req.assigned_to_id else ""}
    req.assigned_to = staff
    req.save(update_fields=["assigned_to", "updated_at"])
    record_audit_event(actor, "REQUEST.ASSIGNED", req, ctx=ctx,
                       changes=diff(before, {"assigned_to": staff.user.username if staff else ""}))
    if staff is not None:
        notify(staff.user, "REQUEST_STATUS", f"{req.number} assigned to you", req.subject,
               route_name="requests:detail", route_kwargs={"request_id": str(req.pk)})
    return req


@transaction.atomic
def set_priority_and_department(actor, req: StudentRequest, priority: str, department, ctx: RequestContext):
    req = StudentRequest.objects.select_for_update(of=("self",)).select_related("category", "student__user").get(
        pk=req.pk)
    authorize(actor, "can_review_request", req, ctx=ctx)
    before = {"priority": req.priority, "department": str(req.department_id or "")}
    req.priority, req.department = priority, department
    if req.assigned_to_id and department is not None and req.assigned_to.department_id != department.pk:
        req.assigned_to = None  # rerouted: the old assignee may no longer be in scope
    req.full_clean(exclude=["student", "category", "number"])
    req.save(update_fields=["priority", "department", "assigned_to", "updated_at"])
    record_audit_event(actor, "REQUEST.ROUTED", req, ctx=ctx,
                       changes=diff(before, {"priority": priority, "department": str(department.pk if department else "")}))
    return req


# --- Transfers ------------------------------------------------------------------------------------


@transaction.atomic
def execute_transfer(actor, req: StudentRequest, ctx: RequestContext, *, program: Program | None = None):
    """Apply an approved transfer to the academic record exactly once (locks: request, transfer, student)."""
    req = StudentRequest.objects.select_for_update(of=("self",)).select_related("category", "student__user").get(
        pk=req.pk)
    authorize(actor, "can_execute_transfer", req, ctx=ctx)
    transfer = TransferRequest.objects.select_for_update(of=("self",)).filter(request=req).first()
    if transfer is None:
        raise RequestError("This is not a transfer request.")
    student = StudentProfile.objects.select_for_update(of=("self",)).select_related("program__department").get(
        pk=req.student_id)
    if req.status != RequestStatus.APPROVED:
        raise RequestError("Only approved transfers can be executed.")
    if transfer.executed_at is not None:
        raise RequestError("This transfer has already been executed.")
    if getattr(settings, "TRANSFER_SEPARATION_OF_DUTIES", True) and req.decided_by_id == actor.pk:
        raise RequestError("The person who approved a transfer cannot also execute it.")
    if (student.program_id != transfer.from_program_id or student.program.department_id != transfer.from_department_id
            or student.campus != transfer.from_campus):
        raise RequestError("The student's record changed since the request was made; review it again.")
    before = {"program": student.program.code, "campus": student.campus}
    if transfer.transfer_type in (TransferType.PROGRAM, TransferType.FACULTY):
        student.program = transfer.to_program
    elif transfer.transfer_type == TransferType.DEPARTMENT:
        if program is None or program.department_id != transfer.to_department_id or not program.is_active:
            raise RequestError("Choose the destination program in the new department.")
        student.program = program
    else:
        student.campus = transfer.to_campus
    student.save(update_fields=["program", "campus", "updated_at"])
    transfer.executed_at, transfer.executed_by = timezone.now(), actor
    transfer.save(update_fields=["executed_at", "executed_by", "updated_at"])
    after = {"program": student.program.code, "campus": student.campus}
    record_audit_event(actor, "TRANSFER.EXECUTED", student, ctx=ctx, changes=diff(before, after))
    record_audit_event(actor, "TRANSFER.EXECUTED", req, ctx=ctx, changes={"after": {"transfer": str(transfer.pk)}})
    notify(req.student.user, "REQUEST_STATUS", f"{req.number}: transfer completed",
           "Your academic record was updated. Check your profile.", route_name="accounts:profile")
    return transfer


# --- Scheduled job --------------------------------------------------------------------------------


def close_stale(days: int | None = None) -> int:
    days = days if days is not None else getattr(settings, "REQUEST_AUTO_CLOSE_DAYS", 14)
    cutoff = timezone.now() - timedelta(days=days)
    closed = 0
    candidates = StudentRequest.objects.filter(
        status__in=[RequestStatus.RESOLVED, RequestStatus.APPROVED, RequestStatus.REJECTED], updated_at__lt=cutoff)
    for req in candidates.select_related("category", "student__user"):
        with transaction.atomic():
            locked = StudentRequest.objects.select_for_update(of=("self",)).select_related(
                "category", "student__user").get(pk=req.pk)
            if locked.status == RequestStatus.APPROVED and _transfer_pending(locked):
                continue  # approved, unexecuted transfers are never auto-closed
            if locked.status not in (RequestStatus.RESOLVED, RequestStatus.APPROVED, RequestStatus.REJECTED):
                continue
            _apply_transition(None, locked, RequestStatus.CLOSED, f"Closed automatically after {days} days.",
                              SYSTEM_CTX)
            closed += 1
    return closed

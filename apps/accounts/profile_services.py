"""Own-profile changes (ARCHITECTURE.md §5.3 rows "Edit own contact fields, photo, emergency contact").

Students change only contact fields and their photo; staff change only their office and phone.
Institutional fields are never accepted here (explicit allowlists below and in the forms).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import StaffProfile, StudentProfile
from apps.core import files
from apps.core.audit import diff, record_audit_event, record_security_event
from apps.core.authz import authorize
from apps.core.context import RequestContext
from apps.core.models import FilePurpose, SecurityEventType

STUDENT_EDITABLE = ("phone", "personal_email", "emergency_contact_name", "emergency_contact_phone",
                    "emergency_contact_relationship")
STAFF_EDITABLE = ("office", "phone")


@transaction.atomic
def update_student_contacts(actor, student: StudentProfile, data: dict, ctx: RequestContext) -> StudentProfile:
    authorize(actor, "can_edit_student_profile", student, ctx=ctx)
    student = StudentProfile.objects.select_for_update().get(pk=student.pk)
    before = {f: getattr(student, f) for f in STUDENT_EDITABLE}
    for field in STUDENT_EDITABLE:
        if field in data:
            setattr(student, field, data[field])
    student.full_clean(exclude=["photo"])
    student.save(update_fields=[*STUDENT_EDITABLE, "updated_at"])
    changes = diff(before, {f: getattr(student, f) for f in STUDENT_EDITABLE})
    if changes["after"]:
        record_audit_event(actor, "PROFILE.CONTACTS_UPDATED", student, ctx=ctx, changes=changes)
    return student


@transaction.atomic
def update_staff_contacts(actor, staff: StaffProfile, data: dict, ctx: RequestContext) -> StaffProfile:
    if staff.user_id != actor.pk:
        authorize(actor, "can_manage_staff", staff, ctx=ctx)
    staff = StaffProfile.objects.select_for_update().get(pk=staff.pk)
    before = {f: getattr(staff, f) for f in STAFF_EDITABLE}
    for field in STAFF_EDITABLE:
        if field in data:
            setattr(staff, field, data[field])
    staff.full_clean()
    staff.save(update_fields=[*STAFF_EDITABLE, "updated_at"])
    changes = diff(before, {f: getattr(staff, f) for f in STAFF_EDITABLE})
    if changes["after"]:
        record_audit_event(actor, "PROFILE.CONTACTS_UPDATED", staff, ctx=ctx, changes=changes)
    return staff


def set_student_photo(actor, student: StudentProfile, upload, ctx: RequestContext) -> StudentProfile:
    authorize(actor, "can_edit_student_profile", student, ctx=ctx)
    try:
        clean = files.validate_upload(upload, FilePurpose.PROFILE_PHOTO)
    except ValidationError as exc:
        record_security_event(SecurityEventType.UPLOAD_REJECTED, ctx=ctx, user=actor,
                              details={"purpose": FilePurpose.PROFILE_PHOTO, "reason": exc.messages[0]})
        raise
    with transaction.atomic():
        student = StudentProfile.objects.select_for_update().get(pk=student.pk)
        old = student.photo
        student.photo = files.store(clean, owner=actor, purpose=FilePurpose.PROFILE_PHOTO)
        student.save(update_fields=["photo", "updated_at"])
        record_audit_event(actor, "PROFILE.PHOTO_UPDATED", student, ctx=ctx,
                           changes={"after": {"photo": str(student.photo_id)}})
    if old is not None:
        files.delete_stored_file(old)
    return student


def remove_student_photo(actor, student: StudentProfile, ctx: RequestContext) -> None:
    authorize(actor, "can_edit_student_profile", student, ctx=ctx)
    with transaction.atomic():
        student = StudentProfile.objects.select_for_update().get(pk=student.pk)
        old = student.photo
        student.photo = None
        student.save(update_fields=["photo", "updated_at"])
        record_audit_event(actor, "PROFILE.PHOTO_REMOVED", student, ctx=ctx)
    if old is not None:
        files.delete_stored_file(old)

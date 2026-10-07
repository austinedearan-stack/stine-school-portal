"""Named policy functions (ARCHITECTURE.md §5.1). One module, one place to review.

Each policy is ``fn(actor, obj) -> bool`` and is registered under its action name. Policies may
assume ``actor`` is authenticated and active (``authorize`` checks that first). Feature phases add
their object policies here; models are imported lazily to keep this module dependency-free.
"""

from __future__ import annotations

from collections.abc import Callable

from apps.core.authz import has_capability
from apps.core.capabilities import Role

POLICIES: dict[str, Callable] = {}


def policy(name: str):
    def register(fn):
        if name in POLICIES:
            raise RuntimeError(f"duplicate policy {name}")
        POLICIES[name] = fn
        return fn

    return register


def capability_policy(name: str, codename: str):
    POLICIES[name] = lambda actor, obj=None: has_capability(actor, codename)


# --- Pure capability policies ---------------------------------------------------------------------
for _name, _cap in [
    ("can_manage_students", "manage_students"),
    ("can_manage_staff", "manage_staff"),
    ("can_manage_academics", "manage_academics"),
    ("can_manage_units", "manage_units"),
    ("can_manage_timetable", "manage_timetable"),
    ("can_manage_hostels", "manage_hostels"),
    ("can_manage_request_config", "manage_request_config"),
    ("can_manage_clubs_admin", "manage_clubs"),
    ("can_view_audit_logs", "view_audit_logs"),
    ("can_view_statistics", "view_statistics"),
    ("can_manage_roles", "manage_roles"),
    ("can_manage_system", "manage_system_settings"),
    ("can_manage_backups", "manage_backups"),
    ("can_manage_grades", "manage_grades"),
    ("can_execute_transfers", "execute_transfers"),
    ("can_approve_transfers", "approve_transfers"),
]:
    capability_policy(_name, _cap)


# --- Accounts -------------------------------------------------------------------------------------


@policy("can_manage_user_account")
def can_manage_user_account(actor, target) -> bool:
    """Activate/deactivate, send a reset code, reset MFA (§5.3 rows "Manage STUDENT/STAFF accounts").

    Accounts that are MFA-required (admins, superadmins, staff holding capabilities) can only be
    managed by holders of manage_roles; nobody manages their own account through these actions.
    """
    from apps.accounts.mfa import is_mfa_required

    if target is None:
        return has_capability(actor, "manage_user_accounts") or has_capability(actor, "manage_roles")
    if target.pk == actor.pk:
        return False
    if is_mfa_required(target) or target.role in Role.ADMINS:
        return has_capability(actor, "manage_roles")
    return has_capability(actor, "manage_user_accounts") or has_capability(actor, "manage_roles")


@policy("can_change_roles")
def can_change_roles(actor, target) -> bool:
    return has_capability(actor, "manage_roles") and (target is None or target.pk != actor.pk)


# --- Students & profiles (audit Z-3: least privilege for student data) ------------------------------


def _staff_teaches_student(actor, student) -> bool:
    from apps.academics.models import LIVE_REGISTRATION_STATUSES, UnitRegistration

    staff = getattr(actor, "staff_profile", None)
    if actor.role != Role.STAFF or staff is None:
        return False
    return UnitRegistration.objects.filter(
        student=student, offering__lecturer=staff, status__in=LIVE_REGISTRATION_STATUSES
    ).exists()


@policy("can_view_student")
def can_view_student(actor, student) -> bool:
    """Own record; holders of manage_students; a lecturer for students in their own offerings (limited fields)."""
    if student is None:
        return has_capability(actor, "manage_students")
    if student.user_id == actor.pk:
        return True
    return has_capability(actor, "manage_students") or _staff_teaches_student(actor, student)


@policy("can_view_student_contacts")
def can_view_student_contacts(actor, student) -> bool:
    """Phone, personal email, emergency contacts: the student and manage_students holders only."""
    return student.user_id == actor.pk or has_capability(actor, "manage_students")


@policy("can_edit_student_profile")
def can_edit_student_profile(actor, student) -> bool:
    """Only the student, and only the student-editable contact fields (enforced by the form allowlist)."""
    return actor.role == Role.STUDENT and student.user_id == actor.pk


@policy("can_view_profile_photo")
def can_view_profile_photo(actor, stored_file) -> bool:
    return stored_file.owner_id == actor.pk or has_capability(actor, "manage_students")


# --- Units & registration -------------------------------------------------------------------------


@policy("can_browse_units")
def can_browse_units(actor, offering=None) -> bool:
    """Every signed-in user may browse the catalogue (draft/cancelled offerings are filtered by the selector)."""
    return True


@policy("can_register_units")
def can_register_units(actor, student) -> bool:
    """Students register and drop only for themselves."""
    return actor.role == Role.STUDENT and student is not None and student.user_id == actor.pk


@policy("can_override_registration")
def can_override_registration(actor, student) -> bool:
    """Registrar override on a student's behalf (window waived; every other rule still applies)."""
    return has_capability(actor, "manage_students") and (student is None or student.user_id != actor.pk)


def _teaches(actor, offering) -> bool:
    staff = getattr(actor, "staff_profile", None)
    return actor.role == Role.STAFF and staff is not None and offering.lecturer_id == staff.pk


@policy("can_view_class_list")
def can_view_class_list(actor, offering) -> bool:
    return _teaches(actor, offering) or any(
        has_capability(actor, c) for c in ("manage_units", "manage_students", "manage_grades"))


@policy("can_record_grade")
def can_record_grade(actor, registration) -> bool:
    if has_capability(actor, "manage_grades"):
        return True
    return has_capability(actor, "record_grades") and _teaches(actor, registration.offering)


# --- Hostels ---------------------------------------------------------------------------------------


@policy("can_book_bed")
def can_book_bed(actor, student) -> bool:
    """Students apply, book, accept and cancel only for themselves (admins allocate via can_manage_hostels)."""
    return actor.role == Role.STUDENT and student is not None and student.user_id == actor.pk


@policy("can_view_hostel_application")
def can_view_hostel_application(actor, application) -> bool:
    """Includes special needs: the applicant and manage_hostels holders only."""
    return application.student.user_id == actor.pk or has_capability(actor, "manage_hostels")


# --- Clubs (audit Z-7) -----------------------------------------------------------------------------


def _is_advisor(actor, club) -> bool:
    staff = getattr(actor, "staff_profile", None)
    return staff is not None and club.advisor_id == staff.pk


def _officer_membership(actor, club):
    from apps.clubs.models import MembershipStatus

    student = getattr(actor, "student_profile", None)
    if actor.role != Role.STUDENT or student is None:
        return None
    return club.memberships.filter(student=student, status=MembershipStatus.APPROVED, can_manage_members=True).first()


@policy("can_manage_club")
def can_manage_club(actor, club) -> bool:
    """Edit club details, appoint officers and remove members: the club's advisor or manage_clubs holders."""
    if club is None:
        return has_capability(actor, "manage_clubs")
    return has_capability(actor, "manage_clubs") or _is_advisor(actor, club)


@policy("can_review_membership")
def can_review_membership(actor, membership) -> bool:
    """Approve/reject/remove: advisor, manage_clubs, or a member-managing officer - never one's own membership."""
    if membership.student.user_id == actor.pk:
        return False
    club = membership.club
    if can_manage_club(actor, club):
        return True
    officer = _officer_membership(actor, club)
    # Officers decide on ordinary members only; officers are appointed and removed by the advisor/office.
    return officer is not None and membership.position == "MEMBER" and not membership.can_manage_members


@policy("can_view_club_members")
def can_view_club_members(actor, club) -> bool:
    return can_manage_club(actor, club) or _officer_membership(actor, club) is not None


@policy("can_manage_club_events")
def can_manage_club_events(actor, club) -> bool:
    return can_manage_club(actor, club) or _officer_membership(actor, club) is not None


@policy("can_act_for_student")
def can_act_for_student(actor, student) -> bool:
    """Self-service student actions (join/leave a club, ...): only the student themself."""
    return actor.role == Role.STUDENT and student is not None and student.user_id == actor.pk


# --- Requests & transfers (audit Z-4, Z-5, Z-8, Z-9) -----------------------------------------------


def _staff_of(actor):
    return getattr(actor, "staff_profile", None) if actor.role != Role.STUDENT else None


def _in_review_scope(actor, req) -> bool:
    """review_requests holders see requests assigned to them or routed to their department."""
    if has_capability(actor, "review_all_requests"):
        return True
    if not has_capability(actor, "review_requests"):
        return False
    staff = _staff_of(actor)
    return staff is not None and (req.assigned_to_id == staff.pk or
                                  (req.department_id is not None and req.department_id == staff.department_id))


def _approver_in_scope(actor, req) -> bool:
    category = req.category
    if not category.requires_approval or not has_capability(actor, category.approval_capability):
        return False
    if req.student.user_id == actor.pk:
        return False  # nobody approves their own request or transfer
    if actor.role in Role.ADMINS:
        return True
    staff = _staff_of(actor)  # staff approvers (heads of department) are scoped to their department
    return staff is not None and (req.department_id == staff.department_id or req.assigned_to_id == staff.pk)


@policy("can_view_request")
def can_view_request(actor, req) -> bool:
    if req.student.user_id == actor.pk:
        return True
    if _in_review_scope(actor, req) or _approver_in_scope(actor, req):
        return True
    return req.category.is_transfer and has_capability(actor, "execute_transfers")


@policy("can_review_request")
def can_review_request(actor, req) -> bool:
    """Triage, assign, request information, resolve, close, re-open."""
    return req.student.user_id != actor.pk and _in_review_scope(actor, req)


@policy("can_approve_request")
def can_approve_request(actor, req) -> bool:
    return _approver_in_scope(actor, req)


@policy("can_execute_transfer")
def can_execute_transfer(actor, req) -> bool:
    return has_capability(actor, "execute_transfers") and req.student.user_id != actor.pk


@policy("can_reply_request")
def can_reply_request(actor, req) -> bool:
    """Owner or in-scope staff may post while the request is open (never on CLOSED/CANCELLED, audit Z-9)."""
    if not req.is_open:
        return False
    return req.student.user_id == actor.pk or can_review_request(actor, req) or _approver_in_scope(actor, req)


@policy("can_download_request_file")
def can_download_request_file(actor, attachment) -> bool:
    req = attachment.request
    if not can_view_request(actor, req):
        return False
    return attachment.visibility == "PUBLIC" or req.student.user_id != actor.pk


@policy("can_use_request_queue")
def can_use_request_queue(actor, obj=None) -> bool:
    return any(has_capability(actor, c) for c in (
        "review_requests", "review_all_requests", "approve_requests", "approve_transfers", "execute_transfers"))

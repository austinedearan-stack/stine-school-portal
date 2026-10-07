from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from rest_framework import permissions

# Role Constants
ROLE_STUDENT = 'STUDENT'
ROLE_STAFF = 'STAFF'
ROLE_ADMIN = 'ADMIN'
ROLE_SUPERADMIN = 'SUPERADMIN'

ALL_ROLES = (ROLE_STUDENT, ROLE_STAFF, ROLE_ADMIN, ROLE_SUPERADMIN)
ADMIN_ROLES = (ROLE_ADMIN, ROLE_SUPERADMIN)
STAFF_AND_ADMIN_ROLES = (ROLE_STAFF, ROLE_ADMIN, ROLE_SUPERADMIN)

# ==========================================
# Centralized Permission Functions
# ==========================================

def is_authenticated_user(user):
    return user is not None and user.is_authenticated and user.is_active

def can_view_student(user, target_student_profile=None):
    """
    Students can only view their own profile.
    Staff and Administrators can view any student profile.
    """
    if not is_authenticated_user(user):
        return False
    if user.role in STAFF_AND_ADMIN_ROLES:
        return True
    if user.role == ROLE_STUDENT:
        if target_student_profile is None:
            return True
        return hasattr(user, 'student_profile') and user.student_profile.pk == target_student_profile.pk
    return False

def can_edit_student_profile(user, target_student_profile):
    """
    Students can edit permitted self-profile fields (phone, photo, emergency contact).
    Admins/Superadmins can edit profiles.
    Staff cannot edit student profiles.
    """
    if not is_authenticated_user(user):
        return False
    if user.role in ADMIN_ROLES:
        return True
    if user.role == ROLE_STUDENT:
        return hasattr(user, 'student_profile') and user.student_profile.pk == target_student_profile.pk
    return False

def can_modify_institutional_records(user):
    """
    Only Admins and Superadmins can modify institutional records (Program, Faculty, Admission date, Disciplinary status).
    Students and Staff NEVER have this permission.
    """
    return is_authenticated_user(user) and user.role in ADMIN_ROLES

def can_browse_units(user):
    return is_authenticated_user(user)

def can_register_units(user, student_profile=None):
    """
    Students can register their own units.
    Superadmins can register for students in exception scenarios.
    """
    if not is_authenticated_user(user):
        return False
    if user.role == ROLE_SUPERADMIN:
        return True
    if user.role == ROLE_STUDENT:
        if student_profile is None:
            return True
        return hasattr(user, 'student_profile') and user.student_profile.pk == student_profile.pk
    return False

def can_manage_units(user):
    """
    Create, edit, activate, deactivate academic units or catalog.
    """
    return is_authenticated_user(user) and user.role in ADMIN_ROLES

def can_view_timetable(user, entry=None):
    return is_authenticated_user(user)

def can_manage_timetable(user):
    """
    Admin and Superadmin can manage timetables.
    Staff can edit entries assigned to them.
    """
    return is_authenticated_user(user) and user.role in ADMIN_ROLES

def can_book_hostel(user, student_profile=None):
    """
    Students can book an available bed for themselves.
    Admin/Superadmin can allocate beds.
    """
    if not is_authenticated_user(user):
        return False
    if user.role in ADMIN_ROLES:
        return True
    if user.role == ROLE_STUDENT:
        if student_profile is None:
            return True
        return hasattr(user, 'student_profile') and user.student_profile.pk == student_profile.pk
    return False

def can_manage_hostels(user):
    return is_authenticated_user(user) and user.role in ADMIN_ROLES

def can_join_club(user, club=None):
    return is_authenticated_user(user) and user.role == ROLE_STUDENT

def can_manage_club(user, club=None):
    """
    Admins, superadmins, or designated staff advisors can manage clubs.
    """
    if not is_authenticated_user(user):
        return False
    if user.role in ADMIN_ROLES:
        return True
    if user.role == ROLE_STAFF and club and hasattr(club, 'advisor') and club.advisor.user_id == user.pk:
        return True
    return False

def can_submit_request(user):
    return is_authenticated_user(user) and user.role == ROLE_STUDENT

def can_view_request(user, request_obj):
    """
    Students can view only their own requests.
    Staff can view requests assigned to their department or person.
    Admins can view all requests.
    """
    if not is_authenticated_user(user):
        return False
    if user.role in ADMIN_ROLES:
        return True
    if user.role == ROLE_STUDENT:
        return hasattr(user, 'student_profile') and request_obj.student_id == user.student_profile.pk
    if user.role == ROLE_STAFF:
        if request_obj.assigned_to and hasattr(user, 'staff_profile') and request_obj.assigned_to_id == user.staff_profile.pk:
            return True
        if hasattr(user, 'staff_profile') and request_obj.category.department_id == user.staff_profile.department_id:
            return True
    return False

def can_review_request(user, request_obj=None):
    if not is_authenticated_user(user):
        return False
    if user.role in ADMIN_ROLES:
        return True
    if user.role == ROLE_STAFF:
        if request_obj is None:
            return True
        if request_obj.assigned_to and hasattr(user, 'staff_profile') and request_obj.assigned_to_id == user.staff_profile.pk:
            return True
        if hasattr(user, 'staff_profile') and request_obj.category.department_id == user.staff_profile.department_id:
            return True
    return False

def can_approve_transfers(user):
    """
    Academic program/department transfers require administrative clearance.
    Staff and students cannot approve transfers.
    """
    return is_authenticated_user(user) and user.role in ADMIN_ROLES

def can_manage_users(user, target_user=None):
    """
    Only Superadmins can manage Admins and Superadmins.
    Admins can manage Staff and Students.
    """
    if not is_authenticated_user(user):
        return False
    if user.role == ROLE_SUPERADMIN:
        return True
    if user.role == ROLE_ADMIN:
        if target_user is None:
            return True
        return target_user.role in (ROLE_STUDENT, ROLE_STAFF)
    return False

def can_view_audit_logs(user):
    return is_authenticated_user(user) and user.role in ADMIN_ROLES

def can_manage_system_security(user):
    return is_authenticated_user(user) and user.role == ROLE_SUPERADMIN

# ==========================================
# View Decorators
# ==========================================

def permission_required(check_func, error_message="You do not have permission to perform this action."):
    """
    Decorator for views enforcing a centralized permission function.
    Raises PermissionDenied (403) or redirects to login if unauthenticated.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if not check_func(request.user):
                raise PermissionDenied(error_message)
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def role_required(*allowed_roles):
    """
    Decorator enforcing that user has one of the allowed roles.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.role not in allowed_roles:
                raise PermissionDenied("Access restricted to authorized roles.")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

# ==========================================
# DRF Permission Classes
# ==========================================

class IsStudentRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return is_authenticated_user(request.user) and request.user.role == ROLE_STUDENT

class IsStaffOrAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return is_authenticated_user(request.user) and request.user.role in STAFF_AND_ADMIN_ROLES

class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return is_authenticated_user(request.user) and request.user.role in ADMIN_ROLES

class IsSuperAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return is_authenticated_user(request.user) and request.user.role == ROLE_SUPERADMIN

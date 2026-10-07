"""Authorization layer (ARCHITECTURE.md §5).

``authorize(actor, action, obj)`` passes only if ALL hold:
  1. the actor is authenticated and active (MFA-verified sessions are enforced by SessionPolicyMiddleware);
  2. the named policy (``apps.core.authz.policies``) returns True - policies check capabilities (bounded by
     role ceilings) and object rules (ownership, assignment, department scope, state).
A refusal raises ``PermissionDenied`` and is recorded (security event + DENIED audit row) on the
independent audit connection, so it survives the request's rollback.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied

from apps.core.capabilities import CAPABILITIES, role_may_hold


def is_active_user(user) -> bool:
    return user is not None and getattr(user, "is_authenticated", False) and user.is_active


def has_capability(user, codename: str) -> bool:
    """True only if the user is active, their role is within the capability's ceiling, and they hold it.

    A grant outside the role ceiling (misconfiguration, stale grant after demotion) is ignored here
    even if it exists in the database (ARCHITECTURE.md §5.2).
    """
    if not is_active_user(user):
        return False
    cap = CAPABILITIES.get(codename)
    if cap is None or not role_may_hold(user.role, codename):
        return False
    return user.has_perm(cap.perm)


def has_any_capability(user) -> bool:
    return any(has_capability(user, codename) for codename in CAPABILITIES)


def capabilities_of(user) -> list[str]:
    return sorted(codename for codename in CAPABILITIES if has_capability(user, codename))


def is_allowed(actor, action: str, obj=None) -> bool:
    from apps.core.authz.policies import POLICIES

    policy = POLICIES[action]  # KeyError = programming error: every action must have a policy
    return is_active_user(actor) and bool(policy(actor, obj))


def authorize(actor, action: str, obj=None, *, ctx=None) -> None:
    if is_allowed(actor, action, obj):
        return
    deny(actor, action, obj, ctx=ctx)


def deny(actor, action: str, obj=None, *, ctx=None, reason: str = "") -> None:
    from apps.core.audit import record_denial, record_security_event
    from apps.core.context import SYSTEM
    from apps.core.models import SecurityEventType

    ctx = ctx or SYSTEM
    details = {"action": action}
    if obj is not None:
        details.update(object_type=type(obj).__name__, object_id=str(getattr(obj, "pk", "")))
    if reason:
        details["reason"] = reason
    record_security_event(
        SecurityEventType.PERMISSION_DENIED, ctx=ctx, user=actor if is_active_user(actor) else None, details=details
    )
    record_denial(actor if is_active_user(actor) else None, f"DENIED.{action}", obj, ctx=ctx, reason=reason)
    raise PermissionDenied("You do not have permission to perform this action.")

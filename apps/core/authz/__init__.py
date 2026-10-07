"""Authorization layer (ARCHITECTURE.md §5). Phase 2 provides the capability check; Phase 3 adds
``authorize()``, object policies and view decorators on top of it."""

from apps.core.capabilities import CAPABILITIES, role_may_hold


def has_capability(user, codename: str) -> bool:
    """True only if the user is active, their role is within the capability's ceiling, and they hold it.

    A grant outside the role ceiling (misconfiguration, stale grant after demotion) is ignored here
    even if it exists in the database (ARCHITECTURE.md §5.2).
    """
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    cap = CAPABILITIES.get(codename)
    if cap is None or not role_may_hold(user.role, codename):
        return False
    return user.has_perm(cap.perm)

"""DRF permission classes mirroring the view decorators (API = same rules as the UI)."""

from rest_framework.permissions import BasePermission

from apps.core.authz import has_capability, is_active_user


class IsActiveUser(BasePermission):
    def has_permission(self, request, view):
        return is_active_user(request.user)


class HasRole(BasePermission):
    roles: tuple = ()

    def has_permission(self, request, view):
        return is_active_user(request.user) and request.user.role in self.roles


class HasCapability(BasePermission):
    capabilities: tuple = ()

    def has_permission(self, request, view):
        return is_active_user(request.user) and any(has_capability(request.user, c) for c in self.capabilities)


def role_permission(*roles):
    return type("RolePermission", (HasRole,), {"roles": roles})


def capability_permission(*codenames):
    return type("CapabilityPermission", (HasCapability,), {"capabilities": codenames})

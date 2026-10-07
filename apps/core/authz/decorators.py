"""View decorators. Unauthenticated users are sent to login; authenticated users without the
right get 403 (and the denial is recorded)."""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login

from apps.core.authz import deny, has_capability, is_active_user, is_allowed
from apps.core.context import RequestContext


def _login_redirect(request):
    return redirect_to_login(request.get_full_path())


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not is_active_user(request.user):
                return _login_redirect(request)
            if request.user.role not in roles:
                deny(request.user, f"role:{'|'.join(roles)}", ctx=RequestContext.from_request(request))
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def capability_required(*codenames):
    """Allow if the user holds ANY of the given capabilities (within their role ceiling)."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not is_active_user(request.user):
                return _login_redirect(request)
            if not any(has_capability(request.user, c) for c in codenames):
                deny(request.user, f"capability:{'|'.join(codenames)}", ctx=RequestContext.from_request(request))
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def policy_required(action: str):
    """Object-less policy gate for list/create views (object checks happen in the service/selector)."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not is_active_user(request.user):
                return _login_redirect(request)
            if not is_allowed(request.user, action):
                deny(request.user, action, ctx=RequestContext.from_request(request))
            return view(request, *args, **kwargs)

        return wrapped

    return decorator

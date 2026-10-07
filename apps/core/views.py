from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@login_required
def dashboard_view(request):
    """Role-aware landing page. Identity comes only from the session (request.user)."""
    return render(request, "core/dashboard.html", {})


# ==========================================
# Production Error Views (Zero Leakage)
# ==========================================

def bad_request_view(request, exception=None):
    return render(request, 'errors/400.html', {'message': 'The server could not process your request due to invalid parameters.'}, status=400)

def permission_denied_view(request, exception=None):
    return render(request, 'errors/403.html', {'message': 'You are not authorized to view or access this resource.'}, status=403)

def not_found_view(request, exception=None):
    return render(request, 'errors/404.html', {'message': 'The requested page or resource could not be found.'}, status=404)

def server_error_view(request):
    # Rendered without the request context: context processors may touch the database, which may be
    # the very thing that failed. The standalone template contains no technical details.
    from django.http import HttpResponseServerError
    from django.template import loader

    return HttpResponseServerError(loader.get_template('errors/500.html').render({}))


def csrf_failure_view(request, reason=""):
    """CSRF failures get the generic 403 page; the technical reason is never shown to the user."""
    return render(request, 'errors/403.html', {'message': 'Your request could not be verified. Please reload the page and try again.'}, status=403)


@never_cache
@require_GET
def health_view(request):
    """Liveness probe for the container orchestrator. Reveals nothing about the system."""
    return HttpResponse("ok", content_type="text/plain")

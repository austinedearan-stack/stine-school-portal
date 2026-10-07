from django.conf import settings
from django.urls import include, path

from apps.core.views import health_view

urlpatterns = [
    path("healthz", health_view, name="health"),
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("academics/", include("apps.academics.urls")),
    path("timetable/", include("apps.timetable.urls")),
    path("hostels/", include("apps.hostels.urls")),
    path("clubs/", include("apps.clubs.urls")),
    # Feature modules are mounted as each is rebuilt on the Phase 2 schema (phases 4-11).
]

# The stock Django admin bypasses MFA, the policy layer and the audit trail (audit finding A-2).
# It is mounted only for DEBUG development when explicitly enabled; production settings refuse it.
if settings.DEBUG and settings.DJANGO_ADMIN_ENABLED:
    from django.contrib import admin

    urlpatterns.append(path("django-admin/", admin.site.urls))

# NOTE: uploaded files are never served from MEDIA_URL (audit finding F-1). Private files are
# returned only by authorization-checked download views.

handler400 = "apps.core.views.bad_request_view"
handler403 = "apps.core.views.permission_denied_view"
handler404 = "apps.core.views.not_found_view"
handler500 = "apps.core.views.server_error_view"

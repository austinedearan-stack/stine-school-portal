from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('django-admin/', admin.site.urls),
    
    # App Routes
    path('', include('apps.core.urls')),
    path('accounts/', include('apps.accounts.urls')),
    path('academics/', include('apps.academics.urls')),
    path('timetable/', include('apps.timetable.urls')),
    path('hostels/', include('apps.hostels.urls')),
    path('clubs/', include('apps.clubs.urls')),
    path('requests/', include('apps.requests.urls')),
    path('notifications/', include('apps.notifications.urls')),
    path('administration/', include('apps.administration.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

handler400 = 'apps.core.views.bad_request_view'
handler403 = 'apps.core.views.permission_denied_view'
handler404 = 'apps.core.views.not_found_view'
handler500 = 'apps.core.views.server_error_view'

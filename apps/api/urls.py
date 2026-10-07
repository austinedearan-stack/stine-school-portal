from django.urls import path

from apps.api import views

app_name = "api"

# Paths as specified in API.md.
urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("me/registrations/", views.MyUnitsView.as_view(), name="my_units"),
    path("me/timetable/", views.MyTimetableView.as_view(), name="timetable"),
    path("me/notifications/", views.NotificationsView.as_view(), name="notifications"),
    path("me/notifications/<uuid:notification_id>/read/", views.NotificationReadView.as_view(),
         name="notification_read"),
    path("offerings/", views.CatalogueView.as_view(), name="units"),
]

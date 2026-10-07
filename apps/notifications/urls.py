from django.urls import path

from apps.notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.inbox_view, name="list"),
    path("<uuid:notification_id>/open/", views.open_view, name="open"),
    path("read-all/", views.mark_all_read_view, name="read_all"),
    path("announcements/", views.announcements_view, name="announcements"),
    path("announcements/manage/", views.manage_view, name="manage"),
    path("announcements/new/", views.announcement_form_view, name="announcement_create"),
    path("announcements/<uuid:announcement_id>/", views.announcement_view, name="announcement"),
    path("announcements/<uuid:announcement_id>/edit/", views.announcement_form_view, name="announcement_edit"),
    path("announcements/<uuid:announcement_id>/withdraw/", views.announcement_withdraw_view, name="withdraw"),
    path("announcements/files/<uuid:attachment_id>/", views.announcement_attachment_view, name="attachment"),
]

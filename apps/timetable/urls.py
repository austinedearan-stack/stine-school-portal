from django.urls import path

from apps.timetable import views

app_name = "timetable"

urlpatterns = [
    path("", views.my_timetable_view, name="my_timetable"),
    path("master/", views.master_view, name="master"),
    path("manage/", views.manage_view, name="manage"),
    path("manage/entries/new/", views.entry_form_view, name="entry_create"),
    path("manage/entries/<uuid:entry_id>/", views.entry_form_view, name="entry_edit"),
    path("manage/entries/<uuid:entry_id>/delete/", views.entry_delete_view, name="entry_delete"),
    path("manage/venues/new/", views.venue_form_view, name="venue_create"),
    path("manage/venues/<uuid:venue_id>/", views.venue_form_view, name="venue_edit"),
]

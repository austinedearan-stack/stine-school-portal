from django.urls import path

from apps.clubs import views

app_name = "clubs"

urlpatterns = [
    path("", views.directory_view, name="directory"),
    path("mine/", views.my_clubs_view, name="my_clubs"),
    path("manage/", views.manage_view, name="manage"),
    path("new/", views.club_form_view, name="create"),
    path("<uuid:club_id>/", views.detail_view, name="detail"),
    path("<uuid:club_id>/edit/", views.club_form_view, name="edit"),
    path("<uuid:club_id>/join/", views.join_view, name="join"),
    path("<uuid:club_id>/leave/", views.leave_view, name="leave"),
    path("<uuid:club_id>/members/", views.members_view, name="members"),
    path("<uuid:club_id>/events/new/", views.event_form_view, name="event_create"),
    path("<uuid:club_id>/events/<uuid:event_id>/", views.event_form_view, name="event_edit"),
    path("memberships/<uuid:membership_id>/decide/", views.decide_view, name="decide"),
    path("memberships/<uuid:membership_id>/remove/", views.remove_view, name="remove"),
    path("memberships/<uuid:membership_id>/appoint/", views.appoint_view, name="appoint"),
]

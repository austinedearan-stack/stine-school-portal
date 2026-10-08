from django.urls import path

from apps.administration import views

app_name = "administration"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("users/", views.users_view, name="users"),
    path("users/new-student/", views.student_create_view, name="student_create"),
    path("users/new-staff/", views.staff_create_view, name="staff_create"),
    path("users/<uuid:user_id>/", views.user_detail_view, name="user_detail"),
    path("users/<uuid:user_id>/<slug:action>/", views.user_action_view, name="user_action"),
    path("students/<uuid:student_id>/edit/", views.student_edit_view, name="student_edit"),
    path("staff/<uuid:staff_id>/edit/", views.staff_edit_view, name="staff_edit"),
    path("setup/", views.setup_index_view, name="academics"),
    path("setup/<slug:kind>/", views.setup_list_view, name="setup_list"),
    path("setup/<slug:kind>/new/", views.setup_form_view, name="setup_create"),
    path("setup/<slug:kind>/<uuid:object_id>/", views.setup_form_view, name="setup_edit"),
    path("setup/units/<uuid:unit_id>/prerequisites/", views.prerequisite_add_view, name="prerequisite_add"),
    path("setup/prerequisites/<uuid:link_id>/remove/", views.prerequisite_remove_view, name="prerequisite_remove"),
    path("setup/offerings/<uuid:offering_id>/lecturer/", views.offering_lecturer_view, name="offering_lecturer"),
    path("setup/semesters/<uuid:semester_id>/current/", views.set_current_semester_view, name="semester_current"),
    path("audit-log/", views.audit_log_view, name="audit_log"),
    path("security-events/", views.security_events_view, name="security_events"),
    path("operations/", views.operations_view, name="operations"),
]

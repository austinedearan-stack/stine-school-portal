from django.urls import path

from apps.student_requests import views

app_name = "requests"

urlpatterns = [
    path("", views.my_requests_view, name="my_requests"),
    path("new/", views.create_view, name="create"),
    path("new/transfer/", views.transfer_create_view, name="create_transfer"),
    path("queue/", views.queue_view, name="queue"),
    path("settings/categories/", views.categories_view, name="categories"),
    path("settings/categories/new/", views.category_form_view, name="category_create"),
    path("settings/categories/<uuid:category_id>/", views.category_form_view, name="category_edit"),
    path("files/<uuid:attachment_id>/", views.attachment_view, name="attachment"),
    path("<uuid:request_id>/", views.detail_view, name="detail"),
    path("<uuid:request_id>/message/", views.message_view, name="message"),
    path("<uuid:request_id>/status/", views.transition_view, name="transition"),
    path("<uuid:request_id>/assign/", views.assign_view, name="assign"),
    path("<uuid:request_id>/routing/", views.routing_view, name="routing"),
    path("<uuid:request_id>/execute-transfer/", views.execute_view, name="execute_transfer"),
]

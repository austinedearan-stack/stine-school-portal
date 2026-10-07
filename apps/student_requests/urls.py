from django.urls import path

from apps.student_requests import views

app_name = 'requests'

urlpatterns = [
    path('', views.student_request_list_view, name='my_requests'),
    path('create/', views.create_request_view, name='create'),
    path('transfer/new/', views.create_transfer_request_view, name='create_transfer'),
    path('ticket/<str:ticket_number>/', views.request_detail_view, name='detail'),
    path('attachment/<uuid:attachment_id>/download/', views.download_attachment_view, name='download_attachment'),
]

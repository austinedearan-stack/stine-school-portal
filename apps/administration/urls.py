from django.urls import path

from apps.administration import views

app_name = 'administration'

urlpatterns = [
    path('', views.admin_dashboard_view, name='dashboard'),
    path('staff-dashboard/', views.staff_dashboard_view, name='staff_dashboard'),
    path('requests/', views.admin_requests_panel_view, name='requests'),
    path('requests/<str:ticket_number>/', views.admin_request_detail_view, name='request_detail'),
    path('transfers/', views.admin_transfers_panel_view, name='transfers'),
    path('transfers/<str:ticket_number>/review/', views.admin_review_transfer_view, name='review_transfer'),
    path('transfers/<str:ticket_number>/execute/', views.admin_execute_transfer_view, name='execute_transfer'),
    path('users/', views.admin_user_management_view, name='users'),
    path('audit-logs/', views.admin_audit_logs_view, name='audit_logs'),
]

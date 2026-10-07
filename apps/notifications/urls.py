from django.urls import path
from apps.notifications import views

app_name = 'notifications'

urlpatterns = [
    path('', views.notification_list_view, name='list'),
    path('mark-all-read/', views.mark_all_read_view, name='mark_all_read'),
    path('announcements/', views.announcement_list_view, name='announcements'),
    path('announcements/<uuid:announcement_id>/', views.announcement_detail_view, name='announcement_detail'),
]

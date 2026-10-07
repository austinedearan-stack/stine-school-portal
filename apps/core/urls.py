from django.urls import path
from apps.core import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('student/dashboard/', views.student_dashboard_view, name='student_dashboard'),
]

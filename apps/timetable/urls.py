from django.urls import path

from apps.timetable import views

app_name = 'timetable'

urlpatterns = [
    path('', views.student_timetable_view, name='student_timetable'),
    path('master/', views.master_timetable_view, name='master'),
]

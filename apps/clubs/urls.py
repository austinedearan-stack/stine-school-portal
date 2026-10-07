from django.urls import path

from apps.clubs import views

app_name = 'clubs'

urlpatterns = [
    path('', views.club_directory_view, name='directory'),
    path('my-clubs/', views.my_clubs_view, name='my_clubs'),
    path('join/', views.join_club_view, name='join'),
    path('leave/', views.leave_club_view, name='leave'),
    path('<str:club_code>/', views.club_detail_view, name='detail'),
]

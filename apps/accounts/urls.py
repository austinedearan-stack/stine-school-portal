from django.urls import path
from apps.accounts import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('mfa/verify/', views.mfa_verify_view, name='mfa_verify'),
    path('mfa/setup/', views.mfa_setup_view, name='mfa_setup'),
    path('profile/', views.profile_view, name='profile'),
    path('profile/edit/', views.edit_student_profile_view, name='edit_profile'),
    path('password-change/', views.password_change_view, name='password_change'),
    path('password-reset/', views.password_reset_request_view, name='password_reset_request'),
]

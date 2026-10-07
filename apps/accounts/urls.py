from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("mfa/verify/", views.mfa_verify_view, name="mfa_verify"),
    path("mfa/enroll/", views.mfa_enroll_view, name="mfa_enroll"),
    path("security/", views.security_view, name="security"),
    path("security/reauth/", views.reauth_view, name="reauth"),
    path("security/mfa/setup/", views.mfa_setup_view, name="mfa_setup"),
    path("security/mfa/recovery-codes/", views.recovery_codes_regenerate_view, name="recovery_codes"),
    path("security/mfa/disable/", views.mfa_disable_view, name="mfa_disable"),
    path("security/sessions/end-others/", views.sign_out_other_sessions_view, name="end_other_sessions"),
    path("password/change/", views.password_change_view, name="password_change"),
    path("password/reset/", views.password_reset_request_view, name="password_reset_request"),
    path("password/reset/confirm/", views.password_reset_confirm_view, name="password_reset_confirm"),
    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.edit_student_profile_view, name="edit_profile"),
]

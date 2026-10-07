"""Security headers, CSRF and error pages (spec §17, §29, §30)."""

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import path


def boom(request):
    raise RuntimeError("SECRET-INTERNAL-DETAIL /etc/passwd SELECT * FROM accounts_user")


urlpatterns_with_error = [path("boom/", boom), path("ok/", lambda r: HttpResponse("ok"))]


@pytest.mark.django_db
def test_security_headers_present(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
    assert response["Referrer-Policy"]
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"


@pytest.mark.django_db
def test_health_endpoint_reveals_nothing(client):
    response = client.get("/healthz")
    assert response.content == b"ok"
    assert client.post("/healthz").status_code == 405


@pytest.mark.django_db
def test_login_without_csrf_token_rejected_generically():
    csrf_client = Client(enforce_csrf_checks=True)
    response = csrf_client.post("/accounts/login/", {"identifier": "x", "password": "y"})
    assert response.status_code == 403
    body = response.content.decode()
    assert "CSRF token" not in body and "Referer" not in body  # no technical reason leaked


@pytest.mark.django_db
def test_custom_404_has_no_debug_information(client):
    response = client.get("/definitely-not-a-page/")
    assert response.status_code == 404
    body = response.content.decode()
    assert "Traceback" not in body and "URLconf" not in body and "portal_config" not in body


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=__name__, DEBUG=False)
def test_500_page_hides_exception_details():
    c = Client(raise_request_exception=False)
    response = c.get("/boom/")
    assert response.status_code == 500
    body = response.content.decode()
    for leak in ("SECRET-INTERNAL-DETAIL", "/etc/passwd", "SELECT", "RuntimeError", "Traceback"):
        assert leak not in body


urlpatterns = [
    *urlpatterns_with_error,
]
handler500 = "apps.core.views.server_error_view"

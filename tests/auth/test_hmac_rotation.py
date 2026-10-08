"""PORTAL_HMAC_KEY rotation: codes and device cookies issued under the old key keep working while it is
listed in PORTAL_HMAC_KEY_FALLBACKS, and stop working once it is removed (DEPLOYMENT.md "Secret rotation")."""

import secrets
from datetime import timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts import devices, mfa, passwords
from apps.accounts.models import MFARecoveryCode, PasswordResetCode
from apps.core.capabilities import Role
from apps.core.crypto import keyed_digest, normalise_code
from tests import factories as f
from tests.conftest import PROD_ENV

pytestmark = pytest.mark.django_db


@pytest.fixture
def rotate(settings):
    def _rotate(*, keep_old: bool):
        old = settings.PORTAL_HMAC_KEY
        settings.PORTAL_HMAC_KEY = secrets.token_urlsafe(48)
        settings.PORTAL_HMAC_KEY_FALLBACKS = [old] if keep_old else []
    return _rotate


@pytest.mark.parametrize("keep_old", [True, False])
def test_recovery_codes_across_rotation(rotate, keep_old):
    user = f.user()
    codes = mfa.issue_recovery_codes(user)
    rotate(keep_old=keep_old)
    assert mfa.use_recovery_code(user, codes[0]) is keep_old


@pytest.mark.parametrize("keep_old", [True, False])
def test_enrollment_codes_across_rotation(rotate, keep_old):
    admin = f.user(Role.ADMIN, groups=["Auditor"])
    code = mfa.issue_enrollment_code(admin, issued_by=None)
    rotate(keep_old=keep_old)
    digest = mfa.matching_enrollment_digest(admin, code)
    assert (digest is not None) is keep_old
    if keep_old:
        assert mfa.consume_enrollment_code(admin, digest)  # the stored digest is what the session keeps


@pytest.mark.parametrize("keep_old", [True, False])
def test_password_reset_codes_across_rotation(rotate, keep_old):
    user = f.user()
    code = "ABCDEFGHJK"
    PasswordResetCode.objects.create(user=user, code_hash=passwords._digest(code),
                                     expires_at=timezone.now() + timedelta(minutes=10))
    rotate(keep_old=keep_old)
    assert passwords.code_is_valid(user, code) is keep_old


@pytest.mark.parametrize("keep_old", [True, False])
def test_trusted_device_cookies_across_rotation(rotate, keep_old, settings):
    from django.http import HttpResponse

    user = f.user()
    response = HttpResponse()
    devices.issue(response, user)
    request = RequestFactory().get("/")
    request.COOKIES[devices.cookie_name()] = response.cookies[devices.cookie_name()].value
    rotate(keep_old=keep_old)
    assert (devices.valid_device_for(request, user) is not None) is keep_old


def test_new_values_are_always_written_with_the_current_key(rotate):
    rotate(keep_old=True)
    user = f.user()
    codes = mfa.issue_recovery_codes(user)
    stored = set(MFARecoveryCode.objects.filter(user=user).values_list("code_hash", flat=True))
    assert keyed_digest(normalise_code(codes[0]), purpose="mfa-recovery") in stored


def test_production_refuses_weak_fallback_keys(py):
    env = {**PROD_ENV, "PORTAL_HMAC_KEY_FALLBACKS": "short"}
    result = py("import django; django.setup()", env, "portal_config.settings.production")
    assert result.returncode != 0 and "PORTAL_HMAC_KEY_FALLBACKS" in result.stderr
    env["PORTAL_HMAC_KEY_FALLBACKS"] = secrets.token_urlsafe(48)
    assert py("import django; django.setup()", env, "portal_config.settings.production").returncode == 0

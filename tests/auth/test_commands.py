import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command

from apps.accounts import mfa
from apps.accounts.models import MFADevice, User
from apps.core.capabilities import Role
from apps.core.crypto import _multi_fernet, decrypt
from tests import factories as f
from tests.helpers import enrol

pytestmark = pytest.mark.django_db


def test_create_portal_superadmin(capsys):
    call_command("create_portal_superadmin", username="root01", email="root01@example.test")
    user = User.objects.get(username="root01")
    out = capsys.readouterr().out
    assert user.role == Role.SUPERADMIN and not user.is_superuser
    assert user.groups.filter(name="Superadmin").exists()
    code = out.split("Enrollment code  : ")[1].split()[0]
    assert mfa.check_enrollment_code(user, code)


def test_rotate_mfa_encryption(settings):
    user = f.user(Role.ADMIN)
    secret = enrol(user)
    old_token = MFADevice.objects.get(user=user).secret_encrypted
    settings.MFA_ENCRYPTION_KEYS = [Fernet.generate_key().decode(), *settings.MFA_ENCRYPTION_KEYS]
    _multi_fernet.cache_clear()
    call_command("rotate_mfa_encryption")
    new_token = MFADevice.objects.get(user=user).secret_encrypted
    assert new_token != old_token and decrypt(new_token) == secret
    settings.MFA_ENCRYPTION_KEYS = settings.MFA_ENCRYPTION_KEYS[:1]  # old key retired
    assert decrypt(MFADevice.objects.get(user=user).secret_encrypted) == secret

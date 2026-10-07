import secrets
import string

import pyotp
from django.contrib.auth import login
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from apps.accounts.models import MFABackupCode, User
from apps.core.permissions import ADMIN_ROLES
from apps.core.utils import get_client_ip, log_audit_event, log_security_event


def generate_totp_secret() -> str:
    return pyotp.random_base32()

def get_totp_provisioning_uri(user: User, secret: str) -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=user.email, issuer_name="University Student Portal")

def generate_backup_codes(user: User, count: int = 8) -> list:
    """
    Generates single-use backup recovery codes. Plaintext codes are returned once to the user;
    only their cryptographic hashes are stored in the database.
    """
    codes = []
    # Invalidate older unused codes for clean state
    user.mfa_backup_codes.filter(is_used=False).delete()

    for _ in range(count):
        alphabet = string.ascii_uppercase + string.digits
        code = ''.join(secrets.choice(alphabet) for _ in range(10))
        formatted_code = f"{code[:5]}-{code[5:]}"
        codes.append(formatted_code)

        MFABackupCode.objects.create(
            user=user,
            code_hash=make_password(formatted_code),
            is_used=False
        )
    return codes

def verify_backup_code(user: User, candidate_code: str) -> bool:
    candidate_clean = candidate_code.strip().upper()
    unused_codes = user.mfa_backup_codes.filter(is_used=False)

    for record in unused_codes:
        if check_password(candidate_clean, record.code_hash):
            record.is_used = True
            record.used_at = timezone.now()
            record.save(update_fields=['is_used', 'used_at'])
            return True
    return False

def verify_totp(user: User, code: str) -> bool:
    """
    Validates a 6-digit TOTP code against user's secret.
    Enforces replay protection: a code used once cannot be reused within the same time window.
    """
    if not user.mfa_secret or not code:
        return False

    code = code.strip()
    if user.last_mfa_used_code == code:
        # Prevent replay attack
        return False

    totp = pyotp.TOTP(user.mfa_secret)
    is_valid = totp.verify(code, valid_window=1)

    if is_valid:
        user.last_mfa_used_code = code
        user.save(update_fields=['last_mfa_used_code'])
        return True
    return False

def authenticate_and_login(request, identifier, password):
    """
    Performs secure authentication:
    - Normalizes username/email
    - Checks account lockout
    - Prevents user enumeration via generic responses
    - Cycles session key on success
    - Handles MFA redirection for privileged accounts
    """
    ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    identifier = identifier.strip()

    # Search by username or email
    user = User.objects.filter(username__iexact=identifier).first() or \
           User.objects.filter(email__iexact=identifier).first()

    if not user:
        log_security_event('LOGIN_FAILURE', username=identifier, ip_address=ip, user_agent=user_agent)
        return None, "INVALID_CREDENTIALS"

    if user.is_locked():
        log_security_event('LOCKOUT', user=user, ip_address=ip, user_agent=user_agent)
        return None, "ACCOUNT_LOCKED"

    if not user.is_active:
        return None, "ACCOUNT_INACTIVE"

    if not user.check_password(password):
        user.register_failed_login()
        log_security_event('LOGIN_FAILURE', user=user, ip_address=ip, user_agent=user_agent)
        if user.is_locked():
            return None, "ACCOUNT_LOCKED"
        return None, "INVALID_CREDENTIALS"

    # Password check passed: reset failed attempts
    user.reset_failed_logins()

    # Privilege check: MFA is strictly mandatory for ADMIN and SUPERADMIN
    if user.role in ADMIN_ROLES or user.is_mfa_enabled:
        if not user.is_mfa_enabled:
            # First-time admin setup requires configuring MFA immediately
            request.session['pre_mfa_user_id'] = str(user.id)
            return user, "MFA_SETUP_REQUIRED"

        request.session['pre_mfa_user_id'] = str(user.id)
        return user, "MFA_REQUIRED"

    # Standard Student or Staff without MFA: Complete login
    return complete_user_login(request, user)


def complete_user_login(request, user):
    """
    Finalizes authentication: logs user in, cycles session key, and writes audit record.
    """
    ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')

    login(request, user)
    # Session rotation to eliminate session fixation
    request.session.cycle_key()

    # Clean pre-mfa session artifacts
    if 'pre_mfa_user_id' in request.session:
        del request.session['pre_mfa_user_id']

    log_security_event('LOGIN_SUCCESS', user=user, ip_address=ip, user_agent=user_agent)
    log_audit_event(
        actor=user,
        action='AUTH_LOGIN',
        target_model='User',
        target_id=str(user.id),
        ip_address=ip,
        user_agent=user_agent,
        details=f"User {user.username} authenticated successfully."
    )
    return user, "SUCCESS"

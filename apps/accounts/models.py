"""Accounts: users, profiles and authentication artifacts (DATABASE.md §2.1).

Lockout counters are deliberately NOT stored on the user row (they live in the rate limiter,
ARCHITECTURE.md §4.1). Codes and nonces are stored only as HMAC digests.
"""

import unicodedata
import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.contrib.auth.validators import ASCIIUsernameValidator
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.capabilities import Role
from apps.core.models import TimeStampedModel


def normalise_identifier(value: str) -> str:
    """NFKC-normalise and trim an identifier, so look-alike Unicode forms (e.g. fullwidth "Ｓ123") collapse to
    one canonical value for storage, lookup and throttling (no homoglyph twin accounts)."""
    return unicodedata.normalize("NFKC", value or "").strip()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        return self.get(username__iexact=username)

    def create_user(self, username, email, password=None, role=Role.STUDENT, **extra_fields):
        if not username:
            raise ValueError("Users must have a username (student/staff ID).")
        if not email:
            raise ValueError("Users must have an email address.")
        if extra_fields.get("is_superuser"):
            raise ValueError("Portal accounts never use is_superuser (ARCHITECTURE.md D4).")
        user = self.model(username=normalise_identifier(username), email=normalise_identifier(self.normalize_email(email)),
                          role=role, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_superuser(self, *args, **kwargs):
        raise NotImplementedError(
            "Use 'manage.py create_portal_superadmin'. Django superusers bypass every permission check (D4)."
        )


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(
        max_length=50, unique=True, validators=[ASCIIUsernameValidator()],
        help_text="Student or staff ID; the login identifier (ASCII letters, digits and @.+-_ only).",
    )
    email = models.EmailField(max_length=254, unique=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=20, choices=Role.CHOICES, default=Role.STUDENT)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Django-admin flag; only meaningful in DEBUG development.")
    date_joined = models.DateTimeField(default=timezone.now)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["username"]
        indexes = [models.Index(fields=["role", "is_active"])]
        constraints = [
            models.UniqueConstraint(Lower("username"), name="user_username_ci_unique"),
            models.UniqueConstraint(Lower("email"), name="user_email_ci_unique"),
            models.CheckConstraint(condition=models.Q(role__in=Role.ALL), name="user_role_valid"),
            models.CheckConstraint(condition=models.Q(is_superuser=False), name="user_never_superuser"),
        ]

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.first_name or self.username

    def set_password(self, raw_password):
        super().set_password(raw_password)
        self.password_changed_at = timezone.now()


class AcademicStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    PROBATION = "PROBATION", "Probation"
    SUSPENDED = "SUSPENDED", "Suspended"
    DEFERRED = "DEFERRED", "Deferred"
    GRADUATED = "GRADUATED", "Graduated"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"


class DisciplinaryStatus(models.TextChoices):
    CLEAR = "CLEAR", "Clear"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    SANCTIONED = "SANCTIONED", "Sanctioned"


class Gender(models.TextChoices):
    FEMALE = "FEMALE", "Female"
    MALE = "MALE", "Male"
    OTHER = "OTHER", "Other"


class StudentProfile(TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="student_profile")

    # Institutional fields: read-only to students; changed only by holders of manage_students.
    student_number = models.CharField(max_length=30, unique=True)
    program = models.ForeignKey("academics.Program", on_delete=models.PROTECT, related_name="students")
    year_of_study = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(8)]
    )
    current_semester_number = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(3)]
    )
    admission_date = models.DateField()
    expected_graduation = models.DateField(null=True, blank=True)
    academic_status = models.CharField(max_length=20, choices=AcademicStatus.choices, default=AcademicStatus.ACTIVE)
    disciplinary_status = models.CharField(
        max_length=20, choices=DisciplinaryStatus.choices, default=DisciplinaryStatus.CLEAR
    )
    campus = models.CharField(max_length=100, default="Main Campus")
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)  # "" = not collected

    # Student-editable fields (explicit allowlist in the profile form).
    phone = models.CharField(max_length=25, blank=True)
    personal_email = models.EmailField(blank=True)
    photo = models.ForeignKey(
        "core.StoredFile", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=25, blank=True)
    emergency_contact_relationship = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["student_number"]
        indexes = [models.Index(fields=["program"]), models.Index(fields=["academic_status"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(year_of_study__gte=1, year_of_study__lte=8), name="student_year_range"
            ),
            models.CheckConstraint(
                condition=models.Q(current_semester_number__gte=1, current_semester_number__lte=3),
                name="student_semester_range",
            ),
            models.CheckConstraint(
                condition=models.Q(academic_status__in=AcademicStatus.values), name="student_academic_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(disciplinary_status__in=DisciplinaryStatus.values),
                name="student_disciplinary_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(gender="") | models.Q(gender__in=Gender.values),
                name="student_gender_valid",
            ),
        ]

    def __str__(self):
        return f"{self.student_number} - {self.user.full_name}"


class StaffProfile(TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="staff_profile")
    staff_number = models.CharField(max_length=30, unique=True)
    department = models.ForeignKey("academics.Department", on_delete=models.PROTECT, related_name="staff_members")
    title = models.CharField(max_length=100, blank=True)
    office = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=25, blank=True)  # visible to admins only
    is_lecturer = models.BooleanField(default=False)

    class Meta:
        ordering = ["staff_number"]

    def __str__(self):
        return f"{self.staff_number} - {self.user.full_name}"


class MFADevice(models.Model):
    """One TOTP authenticator per user. The secret is encrypted at rest (MultiFernet)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="mfa_device")
    secret_encrypted = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Highest TOTP time-step accepted so far; a code is accepted only for a later step (replay guard).
    last_used_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"MFA device for {self.user_id} ({'confirmed' if self.confirmed_at else 'pending'})"


class MFARecoveryCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mfa_recovery_codes")
    code_hash = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "used_at"])]

    def __str__(self):
        return f"Recovery code for {self.user_id} ({'used' if self.used_at else 'unused'})"


class MFAEnrollmentCode(models.Model):
    """One-time code issued out of band; required to enroll a first MFA device (fix for A-1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mfa_enrollment_codes")
    code_hash = models.CharField(max_length=64, unique=True)
    issued_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_enrollment_codes"
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "used_at"])]

    def __str__(self):
        return f"Enrollment code for {self.user_id} ({'used' if self.used_at else 'unused'})"


class PasswordResetCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_codes")
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    created_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "used_at"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(attempts__gte=0, attempts__lte=5), name="reset_attempts_range"),
        ]

    def __str__(self):
        return f"Reset code for {self.user_id} ({'used' if self.used_at else 'unused'})"


class UserSession(models.Model):
    """Index of a user's live session keys, so all of them can be ended at once (deactivation,
    MFA reset, "sign out everywhere") without scanning the session table."""

    session_key = models.CharField(max_length=40, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_index")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"session of {self.user_id}"


class TrustedDevice(models.Model):
    """Backs the signed device cookie used by login throttling (ARCHITECTURE.md §4.1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="trusted_devices")
    nonce_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "revoked_at"])]

    def __str__(self):
        return f"Trusted device for {self.user_id} ({'revoked' if self.revoked_at else 'active'})"

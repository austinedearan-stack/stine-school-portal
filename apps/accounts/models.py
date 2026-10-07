import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.utils import secure_filename


def student_photo_path(instance, filename):
    safe_name = secure_filename(filename, prefix='student_avatar')
    return f"profile_photos/{instance.student_id}/{safe_name}"


class UserManager(BaseUserManager):
    def create_user(self, username, email, password=None, role='STUDENT', **extra_fields):
        if not username:
            raise ValueError('Users must provide a username or identifier.')
        if not email:
            raise ValueError('Users must provide a valid institutional email.')

        email = self.normalize_email(email)
        user = self.model(username=username, email=email, role=role, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'SUPERADMIN')
        return self.create_user(username, email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ('STUDENT', 'Student'),
        ('STAFF', 'Lecturer / Staff'),
        ('ADMIN', 'Administrator'),
        ('SUPERADMIN', 'Super Administrator'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=50, unique=True, db_index=True)
    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STUDENT', db_index=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    # Security & MFA controls
    is_mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=64, blank=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_mfa_used_code = models.CharField(max_length=10, blank=True)
    last_password_change = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    class Meta:
        ordering = ['username']

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username

    def is_locked(self):
        if self.locked_until and timezone.now() < self.locked_until:
            return True
        return False

    def reset_failed_logins(self):
        if self.failed_login_attempts > 0 or self.locked_until:
            self.failed_login_attempts = 0
            self.locked_until = None
            self.save(update_fields=['failed_login_attempts', 'locked_until'])

    def register_failed_login(self, max_attempts=5, lockout_minutes=15):
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.locked_until = timezone.now() + timezone.timedelta(minutes=lockout_minutes)
        self.save(update_fields=['failed_login_attempts', 'locked_until'])


class StudentProfile(models.Model):
    GENDER_CHOICES = (
        ('MALE', 'Male'),
        ('FEMALE', 'Female'),
        ('OTHER', 'Other / Prefer not to say'),
    )
    ACADEMIC_STATUS_CHOICES = (
        ('ACTIVE', 'Active / In Good Standing'),
        ('PROBATION', 'Academic Probation'),
        ('SUSPENDED', 'Suspended'),
        ('GRADUATED', 'Graduated'),
        ('WITHDRAWN', 'Withdrawn'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    student_id = models.CharField(max_length=30, unique=True, db_index=True)

    # Read-Only Institutional Information
    program = models.ForeignKey('academics.Program', on_delete=models.PROTECT, related_name='students')
    admission_date = models.DateField(default=timezone.now)
    academic_status = models.CharField(max_length=20, choices=ACADEMIC_STATUS_CHOICES, default='ACTIVE')
    current_year = models.PositiveSmallIntegerField(default=1)
    current_semester = models.PositiveSmallIntegerField(default=1)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, default='OTHER')

    # Student-Editable Information
    phone = models.CharField(max_length=25, blank=True)
    profile_photo = models.ImageField(upload_to=student_photo_path, null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=25, blank=True)
    emergency_contact_relationship = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['student_id']

    def __str__(self):
        return f"{self.student_id} - {self.user.full_name}"


class StaffProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile')
    staff_id = models.CharField(max_length=30, unique=True, db_index=True)
    department = models.ForeignKey('academics.Department', on_delete=models.PROTECT, related_name='staff_members')
    title = models.CharField(max_length=100, default='Lecturer')
    office_room = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=25, blank=True)

    class Meta:
        ordering = ['staff_id']

    def __str__(self):
        return f"{self.staff_id} - {self.user.full_name} ({self.department.code})"


class MFABackupCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mfa_backup_codes')
    code_hash = models.CharField(max_length=128)
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'is_used']),
        ]

    def __str__(self):
        return f"Backup code for {self.user_id} ({'used' if self.is_used else 'unused'})"

"""Settings shared by every environment.

Environment-specific modules (development, testing, production) import this module and then
tighten or override values. Nothing in this file contains a secret; secrets are read from the
environment and *required* by the environment modules that need them.
"""

from pathlib import Path

from .env import env_bool, env_int, env_list, env_str

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- Core -----------------------------------------------------------------------------------------
# Validated (presence, length) by development.py / production.py; testing.py generates a random key.
SECRET_KEY = env_str("DJANGO_SECRET_KEY")
# Previous keys accepted for verification only, so sessions survive a key rotation (DEPLOYMENT.md).
SECRET_KEY_FALLBACKS = env_list("DJANGO_SECRET_KEY_FALLBACKS")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

# Number of reverse proxies in front of Django whose X-Forwarded-For entries are trusted.
# 0 (default) = use REMOTE_ADDR only; never trust client-supplied forwarding headers by default.
TRUSTED_PROXY_COUNT = env_int("TRUSTED_PROXY_COUNT", 0, minimum=0, maximum=5)

# The stock Django admin bypasses the portal's MFA, policies and audit trail (audit finding A-2).
# It may only be mounted in DEBUG development; production.py refuses to start if this is set.
DJANGO_ADMIN_ENABLED = env_bool("DJANGO_ADMIN_ENABLED", False)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    # Project apps
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.academics.apps.AcademicsConfig",
    "apps.timetable.apps.TimetableConfig",
    "apps.hostels.apps.HostelsConfig",
    "apps.clubs.apps.ClubsConfig",
    "apps.student_requests.apps.StudentRequestsConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.administration.apps.AdministrationConfig",
    "apps.api.apps.ApiConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
    "apps.core.middleware.RequestSizeLimitMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.core.middleware.SessionPolicyMiddleware",
    "apps.core.middleware.UnsafeMethodThrottleMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "portal_config.urls"
WSGI_APPLICATION = "portal_config.wsgi.application"
ASGI_APPLICATION = "portal_config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.portal_context",
            ],
        },
    },
]

# --- Database -------------------------------------------------------------------------------------


def database_from_env() -> dict:
    """Build the default database configuration from DB_* variables."""
    engine = (env_str("DB_ENGINE", "postgresql") or "postgresql").lower()
    if engine == "sqlite":
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / (env_str("DB_NAME", "db.sqlite3") or "db.sqlite3"),
            "OPTIONS": {"timeout": 20},
        }
    if engine != "postgresql":
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("DB_ENGINE must be 'postgresql' or 'sqlite'.")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("DB_NAME", "school_portal"),
        "USER": env_str("DB_USER", "portal_app"),
        "PASSWORD": env_str("DB_PASSWORD", ""),
        "HOST": env_str("DB_HOST", "localhost"),
        "PORT": env_str("DB_PORT", "5432"),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60, minimum=0),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"sslmode": env_str("DB_SSLMODE", "prefer")},
    }


def with_audit_alias(default: dict) -> dict:
    """Add the ``audit`` alias: the same database over an independent autocommit connection.

    Security events and denial audits are written through it so they survive the rollback of the
    request that caused them (ARCHITECTURE.md D16). Migrations never run on it (AuditRouter).
    """
    return {"default": default, "audit": {**default, "TEST": {"MIRROR": "default"}}}


DATABASES = with_audit_alias(database_from_env())
DATABASE_ROUTERS = ["apps.core.routers.AuditRouter"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication -------------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    # Kept only so legacy hashes can be verified once and upgraded to Argon2 on login.
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "apps.accounts.validators.MaximumLengthValidator", "OPTIONS": {"max_length": 128}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Keys for encrypting MFA secrets at rest (comma separated Fernet keys; first encrypts, all decrypt)
# and for keyed hashing of one-time codes. Required in production; see .env.example.
MFA_ENCRYPTION_KEYS = env_list("MFA_ENCRYPTION_KEYS")
PORTAL_HMAC_KEY = env_str("PORTAL_HMAC_KEY")
MFA_ISSUER_NAME = env_str("MFA_ISSUER_NAME", "University Student Portal")

# --- Sessions, cookies, CSRF ----------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
# Idle and absolute lifetimes are enforced by SessionPolicyMiddleware (ARCHITECTURE.md §4.3);
# the cookie age is the longest absolute lifetime.
SESSION_IDLE_TIMEOUT = env_int("SESSION_IDLE_TIMEOUT", 30 * 60, minimum=60)
SESSION_IDLE_TIMEOUT_ADMIN = env_int("SESSION_IDLE_TIMEOUT_ADMIN", 15 * 60, minimum=60)
SESSION_ABSOLUTE_TIMEOUT = env_int("SESSION_ABSOLUTE_TIMEOUT", 12 * 60 * 60, minimum=300)
SESSION_ABSOLUTE_TIMEOUT_ADMIN = env_int("SESSION_ABSOLUTE_TIMEOUT_ADMIN", 4 * 60 * 60, minimum=300)
SESSION_COOKIE_AGE = SESSION_ABSOLUTE_TIMEOUT
DEVICE_COOKIE_NAME = "portal_device"

CSRF_COOKIE_HTTPONLY = True  # templates/HTMX read the token from the page, never from document.cookie
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure_view"

# --- Security headers (production adds HSTS/SSL redirect) -----------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- Request size limits (DoS guard; Nginx enforces its own limit in front) ------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440  # 2.5 MiB of non-file form data
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200
DATA_UPLOAD_MAX_NUMBER_FILES = 10
# Hard cap on any request body, checked from Content-Length before the body is read (Nginx enforces it too).
MAX_REQUEST_BYTES = env_int("MAX_REQUEST_BYTES", 12 * 1024 * 1024, minimum=1024 * 1024)
# Coarse per-user/IP cap on state-changing requests per minute (0 disables); auth endpoints have their own limits.
UNSAFE_REQUESTS_PER_MINUTE = env_int("UNSAFE_REQUESTS_PER_MINUTE", 120, minimum=0)

# --- Internationalisation -------------------------------------------------------------------------
LANGUAGE_CODE = "en"
TIME_ZONE = env_str("PORTAL_TIME_ZONE", "Africa/Nairobi")
USE_I18N = True
USE_TZ = True

# --- Static & private files -----------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded files are PRIVATE: stored outside any web-served directory and only ever returned by
# authorization-checked views (never via MEDIA_URL). See ARCHITECTURE.md D13.
MEDIA_ROOT = Path(env_str("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "var" / "private-media")))
MEDIA_URL = "/private-media-not-served/"

# Requests (ARCHITECTURE.md §5.2, §5.4)
TRANSFER_SEPARATION_OF_DUTIES = env_bool("TRANSFER_SEPARATION_OF_DUTIES", True)
REQUEST_AUTO_CLOSE_DAYS = env_int("REQUEST_AUTO_CLOSE_DAYS", 14, minimum=1)

MAX_PROFILE_PHOTO_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
# Optional malware scanner: dotted path to a callable(bytes) -> bool (True = clean). Off unless configured.
PRIVATE_FILE_SCANNER = env_str("PRIVATE_FILE_SCANNER", "")
# When set (production behind Nginx), downloads are delegated with X-Accel-Redirect to this internal location.
PRIVATE_FILES_X_ACCEL_PREFIX = env_str("PRIVATE_FILES_X_ACCEL_PREFIX", "")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# --- Django REST framework ------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "60/hour", "user": "2000/day"},
    "MAX_PAGINATE_BY": 100,
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
}

# --- Email ----------------------------------------------------------------------------------------
SECURITY_CONTACT = env_str("SECURITY_CONTACT", "mailto:security@example.test")
# Notification kinds that also get an email copy via the outbox (empty = in-app only).
NOTIFICATION_EMAIL_KINDS = env_list("NOTIFICATION_EMAIL_KINDS")
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", "no-reply@example.test")
EMAIL_SUBJECT_PREFIX = "[Student Portal] "

# --- Logging --------------------------------------------------------------------------------------
LOG_LEVEL = env_str("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact": {"()": "apps.core.logging.RedactSecretsFilter"},
        "request_id": {"()": "apps.core.logging.RequestIdFilter"},
    },
    "formatters": {"json": {"()": "apps.core.logging.JsonFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["request_id", "redact"]},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "portal.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

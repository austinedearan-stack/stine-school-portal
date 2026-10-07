"""Build-time settings used only for `collectstatic` inside the Docker build (no secrets, no DB)."""

from django.core.management.utils import get_random_secret_key

from .base import *  # noqa: F403

SECRET_KEY = get_random_secret_key()
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

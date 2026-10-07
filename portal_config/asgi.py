"""ASGI entry point. Defaults to PRODUCTION settings so a misconfigured server never runs with DEBUG."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal_config.settings.production")

application = get_asgi_application()

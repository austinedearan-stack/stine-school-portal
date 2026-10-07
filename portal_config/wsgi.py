"""WSGI entry point. Defaults to PRODUCTION settings so a misconfigured server never runs with DEBUG."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal_config.settings.production")

application = get_wsgi_application()

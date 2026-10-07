from .base import *

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 20,
        },
    }
}

# Cache for local rate limiting & development
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'university-portal-cache',
    }
}

# Local email backend (prints to console for password reset testing)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

"""Outgoing email. Security mail (reset codes, change notices) is sent off the request thread so the
response time does not reveal whether an account exists (no timing oracle on password reset)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("portal.mail")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="portal-mail")


def _send(subject: str, body: str, recipient: str) -> None:
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
    except Exception:  # mail failure must never break or reveal anything in the request
        logger.exception("email delivery failed")


def send_security_mail(subject: str, body: str, recipient: str) -> None:
    if not recipient:
        return
    if getattr(settings, "EMAIL_SEND_SYNC", False):
        _send(subject, body, recipient)
    else:
        _executor.submit(_send, subject, body, recipient)

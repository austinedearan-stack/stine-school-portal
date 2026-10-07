"""Structured JSON logging with secret redaction (spec §24: never log passwords, tokens, cookies)."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|otp|(mfa|recovery|reset|backup|enrol\w*)_?code|cookie|session(id|_key)?$|"
    r"authorization|csrf|api[-_]?key",
    re.IGNORECASE,
)
# key=value / "key": "value" fragments inside free-text messages
SENSITIVE_INLINE = re.compile(
    r"(?P<key>(pass(word|wd)?|secret|token|otp|cookie|sessionid|authorization|csrf\w*|api[-_]?key)"
    r"[\"']?\s*[:=]\s*[\"']?)(?P<value>[^\s\"',;&]+)",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"


def redact(value, depth: int = 0):
    if depth > 6:
        return REDACTED
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and SENSITIVE_KEY.search(k) else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        return SENSITIVE_INLINE.sub(lambda m: m.group("key") + REDACTED, value)
    return value


class RedactSecretsFilter(logging.Filter):
    """Scrubs secrets from the message, its args and any structured ``extra`` fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed format args must never crash logging
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        for key, val in list(record.__dict__.items()):
            if key in _STANDARD_ATTRS:
                continue
            record.__dict__[key] = REDACTED if SENSITIVE_KEY.search(key) else redact(val)
        return True


_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class RequestIdFilter(logging.Filter):
    """Adds the current request id to every log record so log lines can be joined with audit rows."""

    def filter(self, record: logging.LogRecord) -> bool:
        from apps.core.middleware import CURRENT_REQUEST_ID

        if not getattr(record, "request_id", ""):
            record.request_id = CURRENT_REQUEST_ID.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            # Stack traces go to server logs only; they are never rendered to users.
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)

"""Logs must never contain passwords, tokens, cookies or MFA secrets (spec §24)."""

import json
import logging

from apps.core.logging import JsonFormatter, RedactSecretsFilter


def _render(msg, *args, **extra):
    record = logging.LogRecord("portal.security", logging.INFO, __file__, 1, msg, args, None)
    for k, v in extra.items():
        setattr(record, k, v)
    RedactSecretsFilter().filter(record)
    return JsonFormatter().format(record)


def test_inline_secrets_are_redacted():
    out = _render("login password=Hunter2!! token=abc123 sessionid=zzz other=fine")
    assert "Hunter2" not in out and "abc123" not in out and "zzz" not in out
    assert "other=fine" in out


def test_format_args_are_redacted():
    out = _render("payload %s", "csrfmiddlewaretoken=SECRETVALUE")
    assert "SECRETVALUE" not in out


def test_structured_extra_fields_are_redacted():
    out = json.loads(_render("event", data={"password": "p@ss", "mfa_secret": "JBSWY3DP", "user": "s001"},
                             authorization="Bearer xyz"))
    assert out["data"]["password"] == "[REDACTED]"
    assert out["data"]["mfa_secret"] == "[REDACTED]"
    assert out["data"]["user"] == "s001"
    assert out["authorization"] == "[REDACTED]"


def test_status_code_is_not_over_redacted():
    out = json.loads(_render("request", status_code=403))
    assert out["status_code"] == 403

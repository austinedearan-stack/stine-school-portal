"""Writers for the append-only audit trail and security event log (ARCHITECTURE.md §7, D16).

* ``record_audit_event`` writes on the *current* connection, so a business change and its audit
  row commit or roll back together (a change can never commit without its audit row).
* ``record_security_event`` and denial audits use the ``audit`` database alias (an independent
  autocommit connection to the same database) so they survive the rollback of the request that
  triggered them.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import DatabaseError

from apps.core.context import SYSTEM, RequestContext
from apps.core.crypto import keyed_digest
from apps.core.logging import redact
from apps.core.models import AuditLog, Outcome, SecurityEvent

logger = logging.getLogger("portal.security")

AUDIT_DB_ALIAS = "audit"


def _audit_alias() -> str:
    return AUDIT_DB_ALIAS if AUDIT_DB_ALIAS in settings.DATABASES else "default"


def _actor_fields(actor) -> dict[str, Any]:
    if actor is not None and getattr(actor, "is_authenticated", False):
        return {"actor": actor, "actor_identifier": actor.username[:150], "actor_role": getattr(actor, "role", "")}
    return {"actor": None, "actor_identifier": "SYSTEM", "actor_role": ""}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Only the changed keys, as {"before": {...}, "after": {...}}, with secret-looking keys redacted."""
    keys = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "before": redact({k: before.get(k) for k in keys}),
        "after": redact({k: after.get(k) for k in keys}),
    }


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def record_audit_event(
    actor,
    action: str,
    obj=None,
    *,
    ctx: RequestContext = SYSTEM,
    changes: dict | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    outcome: str = Outcome.SUCCESS,
    using: str | None = None,
) -> AuditLog:
    if obj is not None:
        object_type = object_type or type(obj).__name__
        object_id = object_id or str(obj.pk)
    entry = AuditLog(
        **_actor_fields(actor),
        action=action[:100],
        object_type=(object_type or "")[:100],
        object_id=(object_id or "")[:100],
        changes=_jsonable(redact(changes or {})),
        ip_address=ctx.ip,
        user_agent=ctx.user_agent[:256],
        request_id=ctx.request_id[:64],
        outcome=outcome,
    )
    entry.save(using=using or "default")
    return entry


def record_denial(actor, action: str, obj=None, *, ctx: RequestContext = SYSTEM, reason: str = "") -> None:
    """Audit a refused action on the independent connection (survives the request's rollback)."""
    try:
        record_audit_event(
            actor, action, obj, ctx=ctx, changes={"reason": reason} if reason else None,
            outcome=Outcome.DENIED, using=_audit_alias(),
        )
    except DatabaseError:  # never turn a denial into a 500; the log line below still records it
        logger.exception("Failed to persist denial audit event", extra={"action": action})


def hash_identifier(identifier: str) -> str:
    normalised = (identifier or "").strip().lower()
    return keyed_digest(normalised, purpose="identifier") if normalised else ""


def record_security_event(
    event_type: str,
    *,
    ctx: RequestContext = SYSTEM,
    user=None,
    identifier: str | None = None,
    details: dict | None = None,
) -> None:
    """Persist a security event on the independent connection and mirror it to the security log."""
    safe_details = _jsonable(redact(details or {}))
    user_obj = user if (user is not None and getattr(user, "pk", None)) else None
    logger.info(
        "security_event",
        extra={"event_type": event_type, "user_id": str(user_obj.pk) if user_obj else "", "ip": ctx.ip or "",
               "path": ctx.path, "details": safe_details},
    )
    try:
        SecurityEvent(
            event_type=event_type,
            user=user_obj,
            identifier_hash=hash_identifier(identifier) if identifier else "",
            ip_address=ctx.ip,
            user_agent=ctx.user_agent[:256],
            path=ctx.path[:255],
            details=safe_details,
        ).save(using=_audit_alias())
    except DatabaseError:
        logger.exception("Failed to persist security event", extra={"event_type": event_type})

"""Tamper evidence for the append-only streams (DATABASE.md "Append-only enforcement", layer 4).

``seal_stream`` hashes every not-yet-sealed row of a stream that is older than a grace window, in
``seq`` order, chained to the previous seal's hash, and records an ``AuditSeal`` (also written to the
log stream so a copy exists off the database). ``verify_stream`` recomputes every seal from the rows
present today: an edited, deleted or later-inserted row inside a sealed range, or a broken chain, is
reported. A DBA who disables the triggers and rewrites history therefore cannot hide it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditLog, AuditSeal, SealStream, SecurityEvent

logger = logging.getLogger("portal.audit_seal")
GRACE = timedelta(minutes=15)


def _streams():
    from apps.student_requests.models import RequestStatusChange

    return {
        SealStream.AUDIT: (AuditLog, "timestamp"),
        SealStream.SECURITY: (SecurityEvent, "timestamp"),
        SealStream.REQUEST_HISTORY: (RequestStatusChange, "created_at"),
    }


def canonical(row) -> str:
    values = {}
    for field in row._meta.concrete_fields:
        value = getattr(row, field.attname)
        values[field.attname] = value if isinstance(value, bool | int | float | str | dict | list) or value is None \
            else str(value)
    return json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)


def digest(previous: str, rows) -> str:
    h = hashlib.sha256(previous.encode())
    for row in rows:
        h.update(b"\n")
        h.update(canonical(row).encode())
    return h.hexdigest()


@transaction.atomic
def seal_stream(stream: str, *, now=None) -> AuditSeal | None:
    model, time_field = _streams()[stream]
    now = now or timezone.now()
    last = AuditSeal.objects.filter(stream=stream).order_by("-to_seq").first()
    after = last.to_seq if last else 0
    rows = []
    for row in model.objects.filter(seq__gt=after).order_by("seq").iterator():
        if getattr(row, time_field) >= now - GRACE:
            break  # seal only a settled, contiguous prefix; later rows go into the next seal
        rows.append(row)
    if not rows:
        return None
    previous = last.sha256 if last else ""
    seal = AuditSeal(stream=stream, from_seq=rows[0].seq, to_seq=rows[-1].seq, row_count=len(rows),
                     sha256=digest(previous, rows), previous_sha256=previous)
    seal.save()
    logger.info("audit_seal", extra={"stream": stream, "from_seq": seal.from_seq, "to_seq": seal.to_seq,
                                     "rows": seal.row_count, "sha256": seal.sha256})
    return seal


def verify_stream(stream: str) -> list[str]:
    model, _ = _streams()[stream]
    problems = []
    previous_hash, previous_to = "", 0
    for seal in AuditSeal.objects.filter(stream=stream).order_by("from_seq"):
        if seal.previous_sha256 != previous_hash:
            problems.append(f"{stream}: seal {seal.from_seq}-{seal.to_seq} does not chain to the previous seal")
        if seal.from_seq <= previous_to:
            problems.append(f"{stream}: seal {seal.from_seq}-{seal.to_seq} overlaps the previous seal")
        rows = list(model.objects.filter(seq__gte=seal.from_seq, seq__lte=seal.to_seq).order_by("seq"))
        if len(rows) != seal.row_count:
            problems.append(f"{stream}: seal {seal.from_seq}-{seal.to_seq} expected {seal.row_count} rows, "
                            f"found {len(rows)} (rows deleted or inserted)")
        if digest(seal.previous_sha256, rows) != seal.sha256:
            problems.append(f"{stream}: seal {seal.from_seq}-{seal.to_seq} hash mismatch (rows altered)")
        previous_hash, previous_to = seal.sha256, seal.to_seq
    return problems


def seal_all(now=None) -> list[AuditSeal]:
    return [s for s in (seal_stream(stream, now=now) for stream in _streams()) if s is not None]


def verify_all() -> list[str]:
    problems = []
    for stream in _streams():
        problems.extend(verify_stream(stream))
    return problems

"""Per-request metadata passed from views into services (never trusted for identity)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from apps.core.net import get_client_ip, get_user_agent


@dataclass(frozen=True)
class RequestContext:
    ip: str | None = None
    user_agent: str = ""
    request_id: str = ""
    path: str = ""

    @classmethod
    def from_request(cls, request) -> RequestContext:
        request_id = getattr(request, "request_id", "") or uuid.uuid4().hex
        return cls(
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
            request_id=request_id,
            path=(request.path or "")[:255],
        )


SYSTEM = RequestContext(request_id="system")

"""Per-request context (correlation ID) carried through logs, outbox events and agent actions."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str:
    """Current correlation ID; generates one for work started outside a request."""
    rid = _request_id.get()
    if rid is None:
        rid = uuid.uuid4().hex
        _request_id.set(rid)
    return rid


def set_request_id(value: str | None) -> None:
    _request_id.set(value)

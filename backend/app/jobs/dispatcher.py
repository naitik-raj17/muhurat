"""Outbox dispatcher: claims due events, runs their handler, records the outcome.

* Claiming uses `FOR UPDATE SKIP LOCKED` and a lease, so several workers (or API replicas)
  never process the same event twice concurrently, and a crashed worker's events come back.
* The handler runs OUTSIDE the claim transaction. A failure rolls back nothing that was
  already committed: the event goes to `failed` with exponential backoff and is retried;
  after `max_attempts` it becomes `dead` and shows up in the admin view.
* Handlers must be idempotent (e.g. a message already marked sent is not sent again).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_policy_config
from app.core.context import set_request_id
from app.core.logging import get_logger
from app.core.metrics import OUTBOX_EVENTS
from app.db.models import OutboxEvent

log = get_logger(__name__)
LEASE = timedelta(minutes=5)


@dataclass(frozen=True)
class ClaimedEvent:
    id: uuid.UUID
    organization_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    attempts: int
    correlation_id: str | None


class RetryLater(Exception):
    """Raised by a handler to defer without counting an attempt (e.g. quiet hours)."""

    def __init__(self, at: datetime, reason: str) -> None:
        super().__init__(reason)
        self.at = at
        self.reason = reason


Sessions = async_sessionmaker[AsyncSession]
Handler = Callable[[ClaimedEvent, Sessions], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}


def handler(event_type: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _HANDLERS[event_type] = fn
        return fn

    return register


def backoff(attempts: int) -> timedelta:
    cfg = get_policy_config().outbox
    seconds = min(cfg.max_backoff_seconds, cfg.base_backoff_seconds * 2 ** max(0, attempts - 1))
    return timedelta(seconds=seconds)


async def claim_due(sessions: Sessions, limit: int = 20) -> list[ClaimedEvent]:
    now = datetime.now(UTC)
    async with sessions() as s, s.begin():
        rows = (
            (
                await s.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status.in_(("pending", "failed", "processing")),
                        OutboxEvent.next_attempt_at <= now,
                    )
                    .order_by(OutboxEvent.next_attempt_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        claimed = []
        for ev in rows:
            ev.status = "processing"
            ev.attempts += 1
            ev.next_attempt_at = now + LEASE  # lease: if we crash, it is retried after this
            claimed.append(
                ClaimedEvent(
                    id=ev.id,
                    organization_id=ev.organization_id,
                    event_type=ev.event_type,
                    payload=dict(ev.payload),
                    attempts=ev.attempts,
                    correlation_id=ev.correlation_id,
                )
            )
    return claimed


async def _finish(sessions: Sessions, event_id: uuid.UUID, **values: Any) -> None:
    async with sessions() as s, s.begin():
        await s.execute(update(OutboxEvent).where(OutboxEvent.id == event_id).values(**values))


async def process(sessions: Sessions, ev: ClaimedEvent) -> str:
    set_request_id(ev.correlation_id)
    fn = _HANDLERS.get(ev.event_type)
    max_attempts = get_policy_config().outbox.max_attempts
    try:
        if fn is None:
            raise LookupError(f"no handler for {ev.event_type}")
        await fn(ev, sessions)
    except RetryLater as later:
        await _finish(
            sessions,
            ev.id,
            status="pending",
            attempts=ev.attempts - 1,
            next_attempt_at=later.at,
            last_error=f"deferred: {later.reason}",
        )
        return "deferred"
    except Exception as exc:
        dead = ev.attempts >= max_attempts
        log.warning(
            "outbox_failed",
            event_id=str(ev.id),
            event_type=ev.event_type,
            attempts=ev.attempts,
            dead=dead,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
        await _finish(
            sessions,
            ev.id,
            status="dead" if dead else "failed",
            next_attempt_at=datetime.now(UTC) + backoff(ev.attempts),
            last_error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        return "dead" if dead else "failed"
    finally:
        set_request_id(None)
    await _finish(sessions, ev.id, status="sent", sent_at=datetime.now(UTC), last_error=None)
    return "sent"


async def dispatch_once(sessions: Sessions, limit: int = 20) -> dict[str, int]:
    """Process every currently due event (up to `limit`). Returns outcome counts."""
    outcomes: dict[str, int] = {}
    for ev in await claim_due(sessions, limit):
        result = await process(sessions, ev)
        OUTBOX_EVENTS.labels(event_type=ev.event_type, outcome=result).inc()
        outcomes[result] = outcomes.get(result, 0) + 1
    return outcomes

"""Transactional outbox: write the business change and the event in ONE transaction.

External calls (sending a WhatsApp message, analysing a contract with an LLM) never happen
inside a request transaction. The dispatcher (`app.jobs.dispatcher`) performs them later,
with retries and exponential backoff.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_request_id
from app.db.models import OutboxEvent
from app.domain.errors import Conflict, Forbidden
from app.domain.schemas import OutboxEventOut
from app.repositories.repos import OutboxRepository


async def enqueue(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    not_before: datetime | None = None,
) -> uuid.UUID | None:
    """Add an event to the current transaction. Returns None if `dedupe_key` already exists
    (the same reminder/invite was queued before), so retries can never double-send."""
    event_id = uuid.uuid4()
    stmt = (
        insert(OutboxEvent)
        .values(
            id=event_id,
            organization_id=organization_id,
            event_type=event_type,
            payload=payload,
            status="pending",
            attempts=0,
            next_attempt_at=not_before or datetime.now(UTC),
            dedupe_key=dedupe_key,
            correlation_id=get_request_id(),
        )
        .on_conflict_do_nothing(index_elements=[OutboxEvent.dedupe_key])
        .returning(OutboxEvent.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    return inserted


async def list_events(
    session: AsyncSession, status: str | None, limit: int = 100
) -> list[OutboxEventOut]:
    repo = OutboxRepository(session)
    where = [OutboxEvent.status == status] if status else []
    rows = await repo.list(*where, order_by=[OutboxEvent.created_at.desc()], limit=limit)
    return [OutboxEventOut.model_validate(r) for r in rows]


async def status_counts(session: AsyncSession) -> dict[str, int]:
    repo = OutboxRepository(session)
    from sqlalchemy import func

    stmt = (
        select(OutboxEvent.status, func.count())
        .where(repo.scope_filter())
        .group_by(OutboxEvent.status)
    )
    return {s: int(n) for s, n in (await session.execute(stmt)).all()}


async def retry_event(session: AsyncSession, event_id: uuid.UUID) -> OutboxEventOut:
    """Admin action: put a dead (or failed) event back in the queue."""
    event = await OutboxRepository(session).get(event_id)
    if event.status not in ("dead", "failed"):
        raise Conflict(f"Only failed or dead events can be retried (this one is {event.status})")
    if event.organization_id is None:
        raise Forbidden("Not an organization event")
    event.status = "pending"
    event.attempts = 0
    event.next_attempt_at = datetime.now(UTC)
    event.last_error = None
    await session.flush()
    return OutboxEventOut.model_validate(event)

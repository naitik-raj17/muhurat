"""Admin: outbox monitoring (failed / dead events) and manual retry."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.api.deps import DbSession, require
from app.domain.rbac import Permission, Principal
from app.domain.schemas import OutboxEventOut
from app.jobs.worker import kick
from app.services import outbox

router = APIRouter(prefix="/admin", tags=["admin"])
Admin = Annotated[Principal, Depends(require(Permission.ADMIN_OUTBOX))]


@router.get("/outbox", response_model=list[OutboxEventOut])
async def list_outbox(
    _: Admin,
    session: DbSession,
    status: Literal["pending", "processing", "sent", "failed", "dead"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[OutboxEventOut]:
    return await outbox.list_events(session, status, limit)


@router.get("/outbox/summary", response_model=dict[str, int])
async def outbox_summary(_: Admin, session: DbSession) -> dict[str, int]:
    return await outbox.status_counts(session)


@router.post("/outbox/{event_id}/retry", response_model=OutboxEventOut)
async def retry_outbox_event(
    event_id: uuid.UUID, _: Admin, session: DbSession, background: BackgroundTasks
) -> OutboxEventOut:
    out = await outbox.retry_event(session, event_id)
    background.add_task(kick)
    return out

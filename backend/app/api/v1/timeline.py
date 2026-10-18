"""Timeline: draft from templates, view with conflicts, manual edits, lock."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters.factory import get_travel
from app.api.deps import DbSession, require_wedding
from app.domain.rbac import Permission, Principal
from app.domain.schemas import (
    TimelineDraftRequest,
    TimelineItemOut,
    TimelineItemUpdate,
    TimelineView,
)
from app.ports.travel import TravelTimeProvider
from app.services import timeline_service

router = APIRouter(prefix="/weddings/{wedding_id}/timeline", tags=["timeline"])
TimelineWrite = Annotated[Principal, Depends(require_wedding(Permission.TIMELINE_WRITE))]


@router.get("", response_model=TimelineView)
async def get_timeline(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.TIMELINE_READ))],
    session: DbSession,
    travel: Annotated[TravelTimeProvider, Depends(get_travel)],
) -> TimelineView:
    return await timeline_service.view(session, wedding_id, travel)


@router.post("/draft", response_model=list[TimelineItemOut])
async def draft_timeline(
    wedding_id: uuid.UUID,
    body: TimelineDraftRequest,
    principal: TimelineWrite,
    session: DbSession,
) -> list[TimelineItemOut]:
    return await timeline_service.draft_timeline(session, principal, wedding_id, body)


@router.patch("/items/{item_id}", response_model=TimelineItemOut)
async def update_timeline_item(
    wedding_id: uuid.UUID,
    item_id: uuid.UUID,
    body: TimelineItemUpdate,
    principal: TimelineWrite,
    session: DbSession,
) -> TimelineItemOut:
    return await timeline_service.update_item(session, principal, wedding_id, item_id, body)


@router.post("/lock", response_model=dict[str, int])
async def lock_timeline(
    wedding_id: uuid.UUID, _: TimelineWrite, session: DbSession
) -> dict[str, int]:
    return {"locked_items": await timeline_service.lock_timeline(session, wedding_id)}

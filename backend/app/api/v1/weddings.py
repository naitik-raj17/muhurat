"""Weddings, events, overview, couple preferences."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.api.deps import DbSession, require, require_wedding
from app.domain.rbac import Permission, Principal
from app.domain.schemas import (
    EventIn,
    EventOut,
    WeddingCreate,
    WeddingOut,
    WeddingOverview,
    WeddingPreferences,
)
from app.jobs.backend import wedding_changed
from app.services import wedding_service

router = APIRouter(prefix="/weddings", tags=["weddings"])


@router.get("", response_model=list[WeddingOut])
async def list_weddings(
    principal: Annotated[Principal, Depends(require(Permission.WEDDING_READ))], session: DbSession
) -> list[WeddingOut]:
    return await wedding_service.list_weddings(session, principal)


@router.post("", response_model=WeddingOut, status_code=status.HTTP_201_CREATED)
async def create_wedding(
    body: WeddingCreate,
    principal: Annotated[Principal, Depends(require(Permission.WEDDING_WRITE))],
    session: DbSession,
    background: BackgroundTasks,
) -> WeddingOut:
    wedding = await wedding_service.create_wedding(session, body)
    # Temporal mode: start the wedding's long-lived workflow (no-op in-process).
    background.add_task(wedding_changed, wedding.id, principal.organization_id)
    return wedding


@router.get("/{wedding_id}", response_model=WeddingOut)
async def get_wedding(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.WEDDING_READ))],
    session: DbSession,
) -> WeddingOut:
    return WeddingOut.model_validate(await wedding_service.get_wedding(session, wedding_id))


@router.get("/{wedding_id}/overview", response_model=WeddingOverview)
async def overview(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.WEDDING_READ))],
    session: DbSession,
) -> WeddingOverview:
    return await wedding_service.overview(session, wedding_id)


@router.get("/{wedding_id}/events", response_model=list[EventOut])
async def list_events(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.WEDDING_READ))],
    session: DbSession,
) -> list[EventOut]:
    return await wedding_service.list_events(session, wedding_id)


@router.post("/{wedding_id}/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def add_event(
    wedding_id: uuid.UUID,
    body: EventIn,
    _: Annotated[Principal, Depends(require_wedding(Permission.WEDDING_WRITE))],
    session: DbSession,
) -> EventOut:
    return await wedding_service.add_event(session, wedding_id, body)


@router.put("/{wedding_id}/preferences", response_model=WeddingOut)
async def update_preferences(
    wedding_id: uuid.UUID,
    body: WeddingPreferences,
    principal: Annotated[Principal, Depends(require_wedding(Permission.PREFERENCES_SUBMIT))],
    session: DbSession,
) -> WeddingOut:
    return await wedding_service.update_preferences(session, principal, wedding_id, body)

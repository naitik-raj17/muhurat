"""Guest import, listing and RSVPs."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile

from app.api.deps import DbSession, require_wedding
from app.domain.enums import GuestSide, RsvpStatus
from app.domain.rbac import Permission, Principal
from app.domain.schemas import GuestImportReport, GuestPage, GuestRsvpOut, RsvpUpdate
from app.jobs.backend import wedding_changed
from app.services import guest_service

router = APIRouter(prefix="/weddings/{wedding_id}/guests", tags=["guests"])


@router.get("", response_model=GuestPage)
async def list_guests(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.GUEST_READ))],
    session: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    side: GuestSide | None = None,
    rsvp_status: RsvpStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> GuestPage:
    return await guest_service.list_guests(
        session,
        wedding_id,
        search=search,
        side=side,
        rsvp_status=rsvp_status,
        limit=limit,
        offset=offset,
    )


@router.post("/import", response_model=GuestImportReport)
async def import_guests(
    wedding_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_wedding(Permission.GUEST_WRITE))],
    session: DbSession,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="CSV or XLSX guest list")],
) -> GuestImportReport:
    data = await file.read(guest_service.MAX_IMPORT_BYTES + 1)
    report = await guest_service.import_guests(session, wedding_id, file.filename or "", data)
    background.add_task(wedding_changed, wedding_id, principal.organization_id)
    return report


@router.put("/{guest_id}/rsvp", response_model=GuestRsvpOut)
async def update_rsvp(
    wedding_id: uuid.UUID,
    guest_id: uuid.UUID,
    body: RsvpUpdate,
    principal: Annotated[Principal, Depends(require_wedding(Permission.RSVP_SUBMIT))],
    session: DbSession,
) -> GuestRsvpOut:
    return await guest_service.update_rsvp(session, principal, wedding_id, guest_id, body)

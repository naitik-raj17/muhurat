"""Invites, reminders, message log, attendance forecast, simulated replies (mock mode)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import DbSession, require_wedding
from app.core.config import get_settings
from app.domain.errors import Forbidden, ValidationFailed
from app.domain.rbac import Permission, Principal
from app.domain.schemas import (
    AttendanceForecastOut,
    InboundResult,
    InviteRequest,
    MessageOut,
    ReminderRunResult,
    SendResult,
)
from app.jobs.worker import kick
from app.repositories.repos import GuestRepository
from app.services import inbound_service, messaging_service

router = APIRouter(prefix="/weddings/{wedding_id}", tags=["messaging"])
Messaging = Annotated[Principal, Depends(require_wedding(Permission.GUEST_MESSAGE))]
GuestRead = Annotated[Principal, Depends(require_wedding(Permission.GUEST_READ))]


@router.post("/messages/invites", response_model=SendResult)
async def send_invites(
    wedding_id: uuid.UUID,
    body: InviteRequest,
    _: Messaging,
    session: DbSession,
    background: BackgroundTasks,
) -> SendResult:
    result = await messaging_service.send_invites(session, wedding_id, body.event_ids)
    background.add_task(kick)
    return result


@router.post("/messages/reminders", response_model=ReminderRunResult)
async def run_reminders(
    wedding_id: uuid.UUID, _: Messaging, session: DbSession, background: BackgroundTasks
) -> ReminderRunResult:
    result = await messaging_service.run_reminders(session, wedding_id)
    background.add_task(kick)
    return result


@router.get("/messages", response_model=list[MessageOut])
async def list_messages(
    wedding_id: uuid.UUID,
    _: GuestRead,
    session: DbSession,
    guest_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MessageOut]:
    return await messaging_service.list_messages(session, wedding_id, guest_id, limit)


@router.get("/attendance", response_model=AttendanceForecastOut)
async def attendance_forecast(
    wedding_id: uuid.UUID, _: GuestRead, session: DbSession
) -> AttendanceForecastOut:
    return await messaging_service.attendance(session, wedding_id)


class SimulatedReply(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/guests/{guest_id}/simulate-reply", response_model=InboundResult)
async def simulate_reply(
    wedding_id: uuid.UUID,
    guest_id: uuid.UUID,
    body: SimulatedReply,
    _: Messaging,
    session: DbSession,
) -> InboundResult:
    """Mock mode only: behave as if the guest replied on WhatsApp (same path as the webhook)."""
    if get_settings().whatsapp_access_token:
        raise Forbidden("Simulated replies are only available while WhatsApp is mocked")
    guest = await GuestRepository(session).get(guest_id)
    if guest.wedding_id != wedding_id or not guest.phone:
        raise ValidationFailed("Guest has no phone number in this wedding")
    msg = inbound_service.InboundMessage(
        provider_message_id=f"sim-{uuid.uuid4().hex}", from_phone=guest.phone, text=body.text
    )
    return await inbound_service.handle(session, "mock", msg)

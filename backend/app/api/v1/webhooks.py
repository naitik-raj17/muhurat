"""Provider webhooks (no user auth: authenticated by HMAC signature, deduplicated by ID)."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.deps import DbSession
from app.core.config import get_settings
from app.domain.errors import Forbidden, ValidationFailed
from app.domain.schemas import InboundResult
from app.services import inbound_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
MAX_BODY = 256 * 1024


@router.get("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_verify(
    mode: Annotated[str, Query(alias="hub.mode")],
    token: Annotated[str, Query(alias="hub.verify_token")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
) -> str:
    """Meta's subscription handshake."""
    expected = get_settings().whatsapp_verify_token
    if mode != "subscribe" or not expected or token != expected:
        raise Forbidden("Verification failed")
    return challenge


@router.post("/{provider}", response_model=list[InboundResult])
async def receive(
    provider: Literal["whatsapp", "mock"],
    request: Request,
    session: DbSession,
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> list[InboundResult]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ValidationFailed("Payload too large")
    inbound_service.verify_signature(provider, raw, signature)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ValidationFailed("Body is not JSON") from exc
    results = []
    for msg in inbound_service.parse(provider, payload):
        results.append(await inbound_service.handle(session, provider, msg))
    return results

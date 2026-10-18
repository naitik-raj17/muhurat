"""Incoming guest replies (WhatsApp / email / mock provider webhooks).

1. The route verifies the provider signature over the raw body.
2. The provider message ID is inserted into `idempotency_keys`; a duplicate delivery is a no-op.
3. The sender is matched to a guest by the keyed hash of their phone (never by plaintext).
4. The reply text is UNTRUSTED: it is parsed into a validated `RsvpIntent` (rules first, LLM
   only for ambiguous text, wrapped as data). It can only ever set an RSVP status; it can never
   trigger any other action. Low confidence -> no change, the planner sees it in the log.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.factory import get_llm
from app.core.config import get_policy_config, get_settings
from app.core.crypto import pii_hash
from app.core.logging import get_logger
from app.db.models import Guest, IdempotencyKey, Message
from app.db.session import scope_session_to_org
from app.domain.enums import ActorType, RsvpStatus
from app.domain.errors import Unauthorized, ValidationFailed
from app.domain.guest_rules import RsvpIntent, classify_reply
from app.domain.schemas import InboundResult
from app.domain.untrusted import UNTRUSTED_RULES, wrap_untrusted
from app.ports.llm import ChatMessage
from app.services import action_policy
from app.services.guest_service import normalise_phone
from app.services.llm_gateway import LLMGateway, StructuredOutputError

log = get_logger(__name__)


@dataclass(frozen=True)
class InboundMessage:
    provider_message_id: str
    from_phone: str
    text: str
    in_reply_to: str | None = None


def verify_signature(provider: str, raw_body: bytes, header: str | None) -> None:
    """`X-Hub-Signature-256: sha256=<hex hmac of body>` (Meta's scheme; mock uses the same)."""
    s = get_settings()
    secret = s.whatsapp_app_secret if provider == "whatsapp" else s.mock_webhook_secret
    if provider == "whatsapp" and not secret:
        raise Unauthorized("WhatsApp webhooks are not configured (WHATSAPP_APP_SECRET)")
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not header or not hmac.compare_digest(expected, header.strip()):
        raise Unauthorized("Invalid webhook signature")


def parse(provider: str, body: dict[str, Any]) -> list[InboundMessage]:
    if provider == "mock":
        try:
            return [
                InboundMessage(
                    provider_message_id=str(body["message_id"]),
                    from_phone=str(body["from"]),
                    text=str(body.get("text", ""))[:2000],
                    in_reply_to=body.get("in_reply_to"),
                )
            ]
        except KeyError as exc:
            raise ValidationFailed(f"missing field {exc}") from exc
    if provider == "whatsapp":
        out = []
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                for m in change.get("value", {}).get("messages", []):
                    if m.get("type") != "text":
                        continue
                    out.append(
                        InboundMessage(
                            provider_message_id=m["id"],
                            from_phone="+" + m["from"].lstrip("+"),
                            text=m.get("text", {}).get("body", "")[:2000],
                            in_reply_to=(m.get("context") or {}).get("id"),
                        )
                    )
        return out
    raise ValidationFailed(f"unknown provider {provider}")


async def _first_time(session: AsyncSession, provider: str, message_id: str) -> bool:
    stmt = (
        insert(IdempotencyKey)
        .values(id=uuid.uuid4(), scope=f"webhook:{provider}", key=message_id[:200])
        .on_conflict_do_nothing(index_elements=[IdempotencyKey.scope, IdempotencyKey.key])
        .returning(IdempotencyKey.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _find_context(
    session: AsyncSession, phone_hash: str, in_reply_to: str | None
) -> tuple[Guest, Message | None] | None:
    """Cross-tenant lookup (the tenant is unknown until we find the guest). Prefer the exact
    message being replied to, else the guest who most recently got a message on this number."""
    if in_reply_to:
        msg = (
            await session.execute(select(Message).where(Message.provider_message_id == in_reply_to))
        ).scalar_one_or_none()
        if msg and msg.guest_id:
            guest = await session.get(Guest, msg.guest_id)
            if guest and guest.phone_hash == phone_hash:
                return guest, msg
    row = (
        await session.execute(
            select(Guest, Message)
            .join(Message, Message.guest_id == Guest.id)
            .where(Guest.phone_hash == phone_hash, Message.direction == "outbound")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is not None:
        return row[0], row[1]
    guest = (
        (
            await session.execute(
                select(Guest)
                .where(Guest.phone_hash == phone_hash)
                .order_by(Guest.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    return (guest, None) if guest else None


async def _interpret(
    session: AsyncSession, guest: Guest, text: str
) -> tuple[RsvpIntent | None, str]:
    intent = classify_reply(text)
    if intent is not None:
        return intent, ActorType.SYSTEM.value
    gateway = LLMGateway(
        session,
        get_llm(),
        organization_id=guest.organization_id,
        user_id=None,
        wedding_id=guest.wedding_id,
    )
    messages = [
        ChatMessage(
            "system",
            "Classify a wedding guest's reply to an invitation. Return JSON "
            '{"status": "accepted"|"declined"|"maybe"|"pending", "plus_ones": int, '
            '"confidence": 0..1}. Use "pending" when the reply does not answer the invitation. '
            + UNTRUSTED_RULES,
        ),
        ChatMessage("user", wrap_untrusted("guest_reply", text)),
    ]
    try:
        intent, _ = await gateway.structured("guest_comm", messages, RsvpIntent, max_tokens=100)
    except StructuredOutputError:
        return None, ActorType.AI.value
    return intent, ActorType.AI.value


async def handle(session: AsyncSession, provider: str, msg: InboundMessage) -> InboundResult:
    if not await _first_time(session, provider, msg.provider_message_id):
        return InboundResult(duplicate=True, action="ignored_duplicate")
    try:
        phone_hash = pii_hash(normalise_phone(msg.from_phone))
    except ValueError:
        return InboundResult(action="ignored_bad_sender")
    found = await _find_context(session, phone_hash, msg.in_reply_to)
    if found is None:
        log.info("inbound_unknown_sender", provider=provider)
        return InboundResult(action="ignored_unknown_sender")
    guest, context_msg = found
    scope_session_to_org(session, guest.organization_id)
    session.add(
        Message(
            organization_id=guest.organization_id,
            wedding_id=guest.wedding_id,
            guest_id=guest.id,
            event_id=context_msg.event_id if context_msg else None,
            direction="inbound",
            channel="whatsapp" if provider in ("whatsapp", "mock") else provider,
            language=guest.language,
            body=msg.text,
            status="received",
            provider_message_id=msg.provider_message_id,
        )
    )
    intent, _source = await _interpret(session, guest, msg.text)
    threshold = get_policy_config().agents.min_confidence
    if intent is None or intent.status == RsvpStatus.PENDING or intent.confidence < threshold:
        await session.flush()
        return InboundResult(matched_guest=True, action="needs_human_review")

    # Updating a guest's status is a Tier 1 agent action: it goes through the ActionPolicy,
    # so it is audited and can be undone from the activity log.
    action = await action_policy.run_tool(
        session,
        await action_policy.acting_principal(session, guest.organization_id),
        guest.wedding_id,
        "update_guest_status",
        {
            "guest_id": str(guest.id),
            "status": intent.status.value,
            "plus_ones": intent.plus_ones,
            "event_id": str(context_msg.event_id) if context_msg and context_msg.event_id else None,
        },
        agent="guest",
        confidence=intent.confidence,
    )
    if action.status != "executed":
        return InboundResult(matched_guest=True, action="needs_human_review")
    updated = int(action.output.get("updated", 0))
    return InboundResult(
        matched_guest=True, action=f"rsvp_updated:{updated}", rsvp_status=intent.status.value
    )

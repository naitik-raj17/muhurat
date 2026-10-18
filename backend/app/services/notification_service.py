"""Notifications to vendors and planning staff (schedule changes, budget alerts).

Like guest messages, every notification goes through the outbox: the request (or agent action)
only writes an event. Addresses are resolved at send time, so no email address is ever copied
into an outbox payload, and quiet hours apply to these messages too.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.factory import get_channels
from app.core.config import get_policy_config
from app.core.logging import get_logger
from app.db.models import User, Vendor
from app.db.session import scope_session_to_org
from app.domain.enums import Role
from app.domain.guest_rules import in_quiet_hours, next_send_time
from app.jobs.dispatcher import ClaimedEvent, RetryLater, Sessions, handler
from app.ports.messaging import OutboundMessage
from app.services import outbox

log = get_logger(__name__)
NOTIFY_EVENT = "notify.email"
STAFF_ROLES = (Role.ORG_ADMIN.value, Role.PLANNER.value)


async def notify_vendor(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    vendor_id: uuid.UUID,
    subject: str,
    body: str,
    dedupe_key: str,
) -> bool:
    return await _enqueue(
        session,
        organization_id,
        {"to": "vendor", "vendor_id": str(vendor_id), "subject": subject, "body": body},
        dedupe_key,
    )


async def notify_staff(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    subject: str,
    body: str,
    dedupe_key: str,
) -> bool:
    return await _enqueue(
        session, organization_id, {"to": "staff", "subject": subject, "body": body}, dedupe_key
    )


async def _enqueue(
    session: AsyncSession, organization_id: uuid.UUID, payload: dict[str, Any], key: str
) -> bool:
    event_id = await outbox.enqueue(
        session,
        organization_id=organization_id,
        event_type=NOTIFY_EVENT,
        payload=payload,
        dedupe_key=f"notify:{key}"[:200],
    )
    return event_id is not None


async def _recipients(session: AsyncSession, payload: dict[str, Any]) -> list[str]:
    if payload["to"] == "vendor":
        vendor = await session.get(Vendor, uuid.UUID(payload["vendor_id"]))
        return [vendor.contact_email] if vendor and vendor.contact_email else []
    users = (
        await session.execute(
            select(User).where(User.role.in_(STAFF_ROLES), User.is_active.is_(True))
        )
    ).scalars()
    return [u.email for u in users]


@handler(NOTIFY_EVENT)
async def _handle_notify(ev: ClaimedEvent, sessions: Sessions) -> None:
    m = get_policy_config().messaging
    now = datetime.now(UTC)
    if in_quiet_hours(now, m.timezone, m.quiet_hours_start, m.quiet_hours_end):
        raise RetryLater(
            next_send_time(now, m.timezone, m.quiet_hours_start, m.quiet_hours_end),
            "quiet hours (21:00-09:00 IST)",
        )
    async with sessions() as session:
        scope_session_to_org(session, ev.organization_id)
        recipients = await _recipients(session, ev.payload)
    if not recipients:
        log.info("notify_no_recipient", to=ev.payload["to"], event_id=str(ev.id))
        return  # nothing to send is not a failure; the event is done
    channel = get_channels()["email"]
    for i, to in enumerate(recipients):
        await channel.send(
            OutboundMessage(
                channel="email",
                to=to,
                subject=ev.payload["subject"],
                body=ev.payload["body"],
                # Per recipient, so a retry after a partial send never repeats a message.
                dedupe_key=f"{ev.id}:{i}",
            )
        )

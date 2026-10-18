"""Invites, reminders and delivery. Every outgoing message goes through the outbox.

Dedupe key = guest + event + template + reminder number: queuing the same invite or the same
reminder twice is a no-op, so retries and double clicks can never message a guest twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.factory import get_channels
from app.core.config import get_message_templates, get_policy_config
from app.core.logging import get_logger
from app.db.models import Event, Guest, GuestRsvp, Message, Wedding
from app.db.session import scope_session_to_org
from app.domain.enums import RsvpStatus
from app.domain.guest_rules import (
    AttendanceInput,
    forecast_headcount,
    in_quiet_hours,
    next_send_time,
    reminder_due,
)
from app.domain.schemas import (
    AttendanceForecastOut,
    EventAttendance,
    MessageOut,
    ReminderRunResult,
    SendResult,
)
from app.jobs.dispatcher import ClaimedEvent, RetryLater, Sessions, handler
from app.ports.messaging import OutboundMessage
from app.repositories.repos import (
    EventRepository,
    GuestRepository,
    MessageRepository,
    RsvpRepository,
    WeddingRepository,
)
from app.services import outbox

log = get_logger(__name__)
SEND_EVENT = "message.send"


def dedupe_key(guest_id: uuid.UUID, event_id: uuid.UUID, template: str, number: int) -> str:
    return f"{guest_id}:{event_id}:{template}:{number}"


def render(template: str, language: str, values: dict[str, str]) -> tuple[str, str]:
    """(subject, body) in the guest's language, falling back to English."""
    variants = get_message_templates()[template]
    t = variants.get(language) or variants["en"]
    return t["subject"].format(**values), t["body"].format(**values)


def _channel_for(guest: Guest) -> str | None:
    if guest.phone:
        return "whatsapp"
    if guest.email:
        return "email"
    return None


def _send_at(now: datetime) -> datetime:
    m = get_policy_config().messaging
    return next_send_time(now, m.timezone, m.quiet_hours_start, m.quiet_hours_end)


async def _queue_message(
    session: AsyncSession,
    *,
    wedding: Wedding,
    guest: Guest,
    event: Event,
    template: str,
    number: int,
    send_at: datetime,
) -> bool:
    channel = _channel_for(guest)
    if channel is None:
        return False
    key = dedupe_key(guest.id, event.id, template, number)
    subject, body = render(
        template,
        guest.language,
        {
            "guest": guest.name.split()[0],
            "couple": wedding.couple_names,
            "event": event.name,
            "date": event.event_date.strftime("%d %b %Y"),
            "venue": event.venue_name,
        },
    )
    msg_id = uuid.uuid4()
    queued = await outbox.enqueue(
        session,
        organization_id=wedding.organization_id,
        event_type=SEND_EVENT,
        payload={"message_id": str(msg_id)},
        dedupe_key=f"msg:{key}",
        not_before=send_at,
    )
    if queued is None:
        return False  # already queued before: never send twice
    MessageRepository(session).add(
        Message(
            id=msg_id,
            wedding_id=wedding.id,
            guest_id=guest.id,
            event_id=event.id,
            direction="outbound",
            channel=channel,
            template=template,
            language=guest.language,
            body=f"{subject}\n\n{body}" if channel == "email" else body,
            status="queued",
            reminder_number=number,
            dedupe_key=key,
        )
    )
    return True


async def _context(
    session: AsyncSession, wedding_id: uuid.UUID, event_ids: list[uuid.UUID] | None
) -> tuple[Wedding, dict[uuid.UUID, Event], dict[uuid.UUID, Guest], list[GuestRsvp]]:
    wedding = await WeddingRepository(session).get(wedding_id)
    events = {e.id: e for e in await EventRepository(session).for_wedding(wedding_id)}
    if event_ids:
        events = {k: v for k, v in events.items() if k in set(event_ids)}
    guests = {g.id: g for g in await GuestRepository(session).list(Guest.wedding_id == wedding_id)}
    rsvps = await RsvpRepository(session).list(GuestRsvp.event_id.in_(list(events)))
    return wedding, events, guests, rsvps


async def send_invites(
    session: AsyncSession, wedding_id: uuid.UUID, event_ids: list[uuid.UUID] | None
) -> SendResult:
    wedding, events, guests, rsvps = await _context(session, wedding_id, event_ids)
    now = datetime.now(UTC)
    send_at = _send_at(now)
    queued = already = no_contact = 0
    for r in rsvps:
        guest = guests.get(r.guest_id)
        if guest is None:
            continue
        if r.invited_at is not None:
            already += 1
            continue
        if await _queue_message(
            session,
            wedding=wedding,
            guest=guest,
            event=events[r.event_id],
            template="invite",
            number=0,
            send_at=send_at,
        ):
            r.invited_at = now
            queued += 1
        elif _channel_for(guest) is None:
            no_contact += 1
        else:
            already += 1
    if queued:
        wedding.milestones = {**wedding.milestones, "invites_sent": True}
    await session.flush()
    return SendResult(
        queued=queued,
        skipped_already_sent=already,
        skipped_no_contact=no_contact,
        scheduled_for=send_at if send_at > now else None,
    )


async def run_reminders(
    session: AsyncSession, wedding_id: uuid.UUID, now: datetime | None = None
) -> ReminderRunResult:
    """Tier 1: queue due reminders (N days without reply, at most M per guest and event)."""
    now = now or datetime.now(UTC)
    rules = get_policy_config().reminders
    wedding, events, guests, rsvps = await _context(session, wedding_id, None)
    send_at = _send_at(now)
    queued = maxed = no_contact = not_due = 0
    for r in rsvps:
        guest = guests.get(r.guest_id)
        if guest is None or r.status != RsvpStatus.PENDING.value or r.invited_at is None:
            continue
        if r.reminders_sent >= rules.max_reminders:
            maxed += 1
            continue
        if not reminder_due(
            status=r.status,
            invited_at=r.invited_at,
            last_reminder_at=r.last_reminder_at,
            reminders_sent=r.reminders_sent,
            now=now,
            days_without_reply=rules.days_without_reply,
            max_reminders=rules.max_reminders,
        ):
            not_due += 1
            continue
        number = r.reminders_sent + 1
        if await _queue_message(
            session,
            wedding=wedding,
            guest=guest,
            event=events[r.event_id],
            template="reminder",
            number=number,
            send_at=send_at,
        ):
            r.reminders_sent = number
            r.last_reminder_at = now
            queued += 1
        elif _channel_for(guest) is None:
            no_contact += 1
    await session.flush()
    return ReminderRunResult(
        queued=queued,
        skipped_already_sent=not_due,
        skipped_no_contact=no_contact,
        skipped_max_reminders=maxed,
        scheduled_for=send_at if send_at > now else None,
    )


async def list_messages(
    session: AsyncSession, wedding_id: uuid.UUID, guest_id: uuid.UUID | None, limit: int
) -> list[MessageOut]:
    await WeddingRepository(session).get(wedding_id)
    where: list[Any] = [Message.wedding_id == wedding_id]
    if guest_id:
        where.append(Message.guest_id == guest_id)
    rows = await MessageRepository(session).list(
        *where, order_by=[Message.created_at.desc()], limit=limit
    )
    return [MessageOut.model_validate(m) for m in rows]


@handler(SEND_EVENT)
async def _handle_send(ev: ClaimedEvent, sessions: Sessions) -> None:
    """Load (short transaction) -> send (no transaction) -> record (short transaction)."""
    message_id = uuid.UUID(ev.payload["message_id"])
    async with sessions() as session:
        scope_session_to_org(session, ev.organization_id)
        msg = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
        if msg.status == "sent":
            return  # idempotent: a retry after a crash must not send again
        guest = await GuestRepository(session).get(msg.guest_id) if msg.guest_id else None
        to = (guest.phone if msg.channel == "whatsapp" else guest.email) if guest else None
        body, channel, key = msg.body, msg.channel, msg.dedupe_key
        await session.commit()
    if not to:
        raise ValueError("guest has no address for this channel")
    m = get_policy_config().messaging
    now = datetime.now(UTC)
    if in_quiet_hours(now, m.timezone, m.quiet_hours_start, m.quiet_hours_end):
        raise RetryLater(_send_at(now), "quiet hours (21:00-09:00 IST)")

    subject = body.split("\n\n", 1)[0] if channel == "email" else None
    try:
        result = await get_channels()[channel].send(
            OutboundMessage(channel=channel, to=to, body=body, subject=subject, dedupe_key=key)
        )
    except Exception as exc:
        async with sessions() as session, session.begin():
            scope_session_to_org(session, ev.organization_id)
            row = (
                await session.execute(select(Message).where(Message.id == message_id))
            ).scalar_one()
            row.status = "retrying"
            row.error = f"{type(exc).__name__}: {exc}"[:500]
        raise
    async with sessions() as session, session.begin():
        scope_session_to_org(session, ev.organization_id)
        row = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
        row.status = result.status
        row.provider_message_id = result.provider_message_id
        row.sent_at = datetime.now(UTC)
        row.error = None


# --------------------------------------------------------------------------- attendance


async def attendance(session: AsyncSession, wedding_id: uuid.UUID) -> AttendanceForecastOut:
    _, events, guests, rsvps = await _context(session, wedding_id, None)
    by_event: dict[uuid.UUID, list[GuestRsvp]] = {}
    for r in rsvps:
        by_event.setdefault(r.event_id, []).append(r)
    out = []
    for event_id, event in sorted(events.items(), key=lambda kv: kv[1].start_at):
        rows = by_event.get(event_id, [])
        counts: dict[str, int] = {}
        inputs = []
        for r in rows:
            counts[r.status] = counts.get(r.status, 0) + 1
            g = guests.get(r.guest_id)
            if g is None:
                continue
            inputs.append(
                AttendanceInput(
                    status=r.status,
                    plus_ones=r.plus_ones,
                    side=g.side,
                    distance_km=g.distance_km,
                    past_response_rate=g.past_response_rate,
                    is_vip=g.is_vip,
                )
            )
        f = forecast_headcount(inputs)
        out.append(
            EventAttendance(
                event_id=event_id,
                event_name=event.name,
                invited=len(rows),
                confirmed_headcount=f.confirmed,
                expected_headcount=f.expected,
                low=f.low,
                high=f.high,
                counts=counts,
            )
        )
    return AttendanceForecastOut(
        events=out,
        method="Per-guest attendance probability (RSVP status, distance, past response rate, "
        "VIP) summed per event; 90% range from the normal approximation.",
    )

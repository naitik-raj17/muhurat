"""Weddings, events and the overview dashboard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Wedding
from app.domain.enums import ShortlistStatus
from app.domain.errors import Forbidden
from app.domain.rbac import WEDDING_SCOPED_ROLES, Principal
from app.domain.schemas import (
    BudgetSummary,
    EventIn,
    EventOut,
    UpcomingTask,
    WeddingCreate,
    WeddingOut,
    WeddingOverview,
    WeddingPreferences,
)
from app.repositories.repos import (
    BudgetRepository,
    EventRepository,
    GuestRepository,
    RsvpRepository,
    ShortlistRepository,
    TimelineRepository,
    WeddingRepository,
)


async def list_weddings(session: AsyncSession, principal: Principal) -> list[WeddingOut]:
    repo = WeddingRepository(session)
    where = []
    if principal.role in WEDDING_SCOPED_ROLES:
        # Wedding-scoped roles (client) only ever see their own wedding.
        where.append(Wedding.id == principal.wedding_id)
    weddings = await repo.list(*where, order_by=[Wedding.start_date])
    return [WeddingOut.model_validate(w) for w in weddings]


async def get_wedding(session: AsyncSession, wedding_id: uuid.UUID) -> Wedding:
    return await WeddingRepository(session).get(wedding_id)


def _event_from(data: EventIn, wedding_id: uuid.UUID) -> Event:
    return Event(
        wedding_id=wedding_id,
        kind=data.kind.value,
        name=data.name or data.kind.value.title(),
        event_date=data.event_date,
        venue_name=data.venue_name,
        venue_city=data.venue_city,
        start_at=data.start_at,
        end_at=data.end_at,
        expected_guests=data.expected_guests,
    )


async def create_wedding(session: AsyncSession, data: WeddingCreate) -> WeddingOut:
    repo = WeddingRepository(session)
    wedding = repo.add(
        Wedding(
            name=data.name,
            couple_names=data.couple_names,
            city=data.city,
            start_date=data.start_date,
            end_date=data.end_date,
            total_budget=data.total_budget,
            styles=[s.lower() for s in data.styles],
            expected_guests=data.expected_guests,
            preferences={},
            milestones={},
        )
    )
    await session.flush()
    events = EventRepository(session)
    for ev in data.events:
        events.add(_event_from(ev, wedding.id))
    await session.flush()
    return WeddingOut.model_validate(wedding)


async def add_event(session: AsyncSession, wedding_id: uuid.UUID, data: EventIn) -> EventOut:
    await get_wedding(session, wedding_id)
    event = EventRepository(session).add(_event_from(data, wedding_id))
    await session.flush()
    return EventOut.model_validate(event)


async def list_events(session: AsyncSession, wedding_id: uuid.UUID) -> list[EventOut]:
    await get_wedding(session, wedding_id)
    events = await EventRepository(session).for_wedding(wedding_id)
    return [EventOut.model_validate(e) for e in events]


async def update_preferences(
    session: AsyncSession, principal: Principal, wedding_id: uuid.UUID, prefs: WeddingPreferences
) -> WeddingOut:
    if principal.is_ai:
        raise Forbidden("Preferences are submitted by people, not agents")
    wedding = await get_wedding(session, wedding_id)
    wedding.preferences = {
        **prefs.model_dump(),
        "submitted_by": str(principal.user_id),
        "submitted_at": datetime.now(UTC).isoformat(),
    }
    await session.flush()
    return WeddingOut.model_validate(wedding)


async def budget_summary(session: AsyncSession, wedding: Wedding) -> BudgetSummary:
    planned, actual = await BudgetRepository(session).totals(wedding.id)
    return BudgetSummary(
        total_budget=wedding.total_budget,
        planned=planned,
        actual=actual,
        remaining=wedding.total_budget - actual,
    )


async def overview(session: AsyncSession, wedding_id: uuid.UUID) -> WeddingOverview:
    wedding = await get_wedding(session, wedding_id)
    events = await EventRepository(session).for_wedding(wedding_id)
    guest_count = await GuestRepository(session).count_for_wedding(wedding_id)
    rsvp_counts = await RsvpRepository(session).status_counts(wedding_id)
    sl_counts = await ShortlistRepository(session).status_counts(wedding_id)
    upcoming_items = await TimelineRepository(session).upcoming(wedding_id, datetime.now(UTC), 5)

    upcoming = [UpcomingTask(title=i.title, due=i.start_at, kind=i.kind) for i in upcoming_items]
    if rsvp_counts.get("pending"):
        upcoming.insert(
            0,
            UpcomingTask(
                title=f"{rsvp_counts['pending']} RSVPs still pending", due=None, kind="guests"
            ),
        )
    if not sl_counts.get(ShortlistStatus.SELECTED.value):
        upcoming.insert(0, UpcomingTask(title="No vendors selected yet", due=None, kind="vendors"))

    return WeddingOverview(
        wedding=WeddingOut.model_validate(wedding),
        events=[EventOut.model_validate(e) for e in events],
        guest_count=guest_count,
        rsvp_counts=rsvp_counts,
        shortlist_count=sum(sl_counts.values()),
        selected_vendor_count=sl_counts.get(ShortlistStatus.SELECTED.value, 0),
        budget=await budget_summary(session, wedding),
        upcoming=upcoming,
    )

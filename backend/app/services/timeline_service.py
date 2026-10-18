"""Timeline drafting from templates, manual edits, and conflict detection."""

from __future__ import annotations

import uuid
from itertools import combinations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_timeline_templates
from app.db.models import Event, TimelineItem, Vendor
from app.domain.enums import TimelineItemStatus
from app.domain.errors import Conflict, ValidationFailed
from app.domain.rbac import Principal
from app.domain.schemas import (
    TimelineConflict,
    TimelineDraftRequest,
    TimelineItemOut,
    TimelineItemUpdate,
    TimelineView,
)
from app.domain.timeline import (
    EventWindow,
    ItemView,
    TemplateItem,
    detect_conflicts,
    draft_event_items,
)
from app.ports.travel import TravelTimeProvider
from app.repositories.repos import (
    EventRepository,
    TimelineRepository,
    VendorRepository,
    WeddingRepository,
)
from app.services.vendor_service import selected_vendor_ids


def _templates() -> tuple[dict[str, list[TemplateItem]], int]:
    cfg = get_timeline_templates()
    templates = {
        kind: [TemplateItem(**item) for item in spec["items"]]
        for kind, spec in cfg["events"].items()
    }
    return templates, int(cfg["default_buffer_minutes"])


async def draft_timeline(
    session: AsyncSession,
    principal: Principal,
    wedding_id: uuid.UUID,
    req: TimelineDraftRequest,
) -> list[TimelineItemOut]:
    """Generate draft items for every event from the templates, assigning selected vendors.

    Locked items are never replaced; with `replace_existing` the old *draft* items are removed.
    """
    await WeddingRepository(session).get(wedding_id)
    templates, default_buffer = _templates()
    buffer = req.buffer_minutes if req.buffer_minutes is not None else default_buffer
    events = await EventRepository(session).for_wedding(wedding_id)
    if not events:
        raise ValidationFailed("Add the wedding's events before drafting a timeline")

    repo = TimelineRepository(session)
    existing = await repo.for_wedding(wedding_id)
    if existing and not req.replace_existing:
        raise Conflict("A timeline already exists; pass replace_existing=true to redraft")
    locked_events = {i.event_id for i in existing if i.status == TimelineItemStatus.LOCKED.value}
    for item in existing:
        if item.status != TimelineItemStatus.LOCKED.value:
            await repo.delete(item)
    await session.flush()

    vendor_ids = await selected_vendor_ids(session, wedding_id)
    vendors = await VendorRepository(session).list(Vendor.id.in_(vendor_ids)) if vendor_ids else []
    by_category: dict[str, uuid.UUID] = {}
    for v in sorted(vendors, key=lambda v: (-v.rating, v.name)):
        by_category.setdefault(v.category, v.id)

    created: list[TimelineItem] = []
    for ev in events:
        if ev.id in locked_events or ev.kind not in templates:
            continue
        planned = draft_event_items(
            event_id=ev.id,
            event_start=ev.start_at,
            location=ev.venue_name,
            template=templates[ev.kind],
            buffer_minutes=buffer,
            vendor_for_category=by_category,
        )
        for p in planned:
            created.append(
                repo.add(
                    TimelineItem(
                        wedding_id=wedding_id,
                        event_id=p.event_id,
                        title=p.title,
                        kind=p.kind,
                        start_at=p.start_at,
                        end_at=p.end_at,
                        location=p.location,
                        vendor_id=p.vendor_id,
                        vendor_category=p.vendor_category,
                        buffer_after_minutes=p.buffer_after_minutes,
                        status=TimelineItemStatus.DRAFT.value,
                        created_by_actor=principal.actor_type.value,
                        sort_order=p.sort_order,
                    )
                )
            )
    await session.flush()
    return [TimelineItemOut.model_validate(i) for i in created]


async def _travel_lookup(
    items: list[TimelineItem], city: str, travel: TravelTimeProvider
) -> dict[tuple[str, str], int]:
    places = sorted({i.location.strip().lower() for i in items})
    table: dict[tuple[str, str], int] = {}
    for a, b in combinations(places, 2):
        minutes = await travel.minutes_between(a, b, city)
        table[(a, b)] = table[(b, a)] = minutes
    return table


async def view(
    session: AsyncSession, wedding_id: uuid.UUID, travel: TravelTimeProvider
) -> TimelineView:
    wedding = await WeddingRepository(session).get(wedding_id)
    items = await TimelineRepository(session).for_wedding(wedding_id)
    events = await EventRepository(session).for_wedding(wedding_id)
    conflicts = await conflicts_for(items, events, wedding.city, travel)
    return TimelineView(
        items=[TimelineItemOut.model_validate(i) for i in items], conflicts=conflicts
    )


async def conflicts_for(
    items: list[TimelineItem], events: list[Event], city: str, travel: TravelTimeProvider
) -> list[TimelineConflict]:
    table = await _travel_lookup(items, city, travel)
    _, buffer = _templates()

    def travel_minutes(a: str, b: str) -> int:
        a, b = a.strip().lower(), b.strip().lower()
        return 0 if a == b else table.get((a, b), 0)

    found = detect_conflicts(
        [
            ItemView(
                id=i.id,
                event_id=i.event_id,
                title=i.title,
                kind=i.kind,
                start_at=i.start_at,
                end_at=i.end_at,
                location=i.location,
                vendor_id=i.vendor_id,
            )
            for i in items
        ],
        events={e.id: EventWindow(e.start_at, e.end_at, e.name) for e in events},
        travel_minutes=travel_minutes,
        buffer_minutes=buffer,
    )
    return [
        TimelineConflict(
            kind=c.kind,
            severity=c.severity,
            message=c.message,
            item_ids=c.item_ids,
            suggestion=c.suggestion,
        )
        for c in found
    ]


async def update_item(
    session: AsyncSession,
    principal: Principal,
    wedding_id: uuid.UUID,
    item_id: uuid.UUID,
    data: TimelineItemUpdate,
) -> TimelineItemOut:
    """Manual edit by a person. Agent edits go through the ActionPolicy (Tier 2)."""
    item = await TimelineRepository(session).get(item_id)
    if item.wedding_id != wedding_id:
        raise ValidationFailed("Timeline item belongs to another wedding")
    changes = data.model_dump(exclude_unset=True)
    if "vendor_id" in changes and changes["vendor_id"] is not None:
        await VendorRepository(session).get(changes["vendor_id"])
    for key, value in changes.items():
        setattr(item, key, value)
    if item.end_at <= item.start_at:
        raise ValidationFailed("end_at must be after start_at")
    await session.flush()
    return TimelineItemOut.model_validate(item)


async def lock_timeline(session: AsyncSession, wedding_id: uuid.UUID) -> int:
    items = await TimelineRepository(session).for_wedding(wedding_id)
    for i in items:
        i.status = TimelineItemStatus.LOCKED.value
    wedding = await WeddingRepository(session).get(wedding_id)
    wedding.milestones = {**wedding.milestones, "timeline_locked": True}
    await session.flush()
    return len(items)

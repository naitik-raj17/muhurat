"""Timeline drafting and conflict detection. Pure functions: no I/O, no framework imports.

The service layer loads events, vendors and travel times, then calls into this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

# Items of these kinds happen before guests arrive and may run in parallel at the venue.
PRE_EVENT_KINDS = frozenset({"setup", "prep"})


@dataclass(frozen=True)
class TemplateItem:
    title: str
    minutes: int
    kind: str
    vendor_category: str | None = None


@dataclass
class PlannedItem:
    event_id: uuid.UUID
    title: str
    kind: str
    start_at: datetime
    end_at: datetime
    location: str
    vendor_category: str | None
    vendor_id: uuid.UUID | None
    buffer_after_minutes: int
    sort_order: int


def draft_event_items(
    *,
    event_id: uuid.UUID,
    event_start: datetime,
    location: str,
    template: Sequence[TemplateItem],
    buffer_minutes: int,
    vendor_for_category: Mapping[str, uuid.UUID],
) -> list[PlannedItem]:
    """Lay out one event: setup/prep items finish when the event starts (in parallel);
    every other item runs back to back from the event start with `buffer_minutes` between."""
    items: list[PlannedItem] = []
    cursor = event_start
    for order, t in enumerate(template):
        vendor_id = vendor_for_category.get(t.vendor_category) if t.vendor_category else None
        if t.kind in PRE_EVENT_KINDS:
            start, end, buffer = event_start - timedelta(minutes=t.minutes), event_start, 0
        else:
            start, end, buffer = cursor, cursor + timedelta(minutes=t.minutes), buffer_minutes
            cursor = end + timedelta(minutes=buffer_minutes)
        items.append(
            PlannedItem(
                event_id=event_id,
                title=t.title,
                kind=t.kind,
                start_at=start,
                end_at=end,
                location=location,
                vendor_category=t.vendor_category,
                vendor_id=vendor_id,
                buffer_after_minutes=buffer,
                sort_order=order,
            )
        )
    # The last main item needs no trailing buffer.
    mains = [i for i in items if i.kind not in PRE_EVENT_KINDS]
    if mains:
        mains[-1].buffer_after_minutes = 0
    return sorted(items, key=lambda i: (i.start_at, i.sort_order))


# --------------------------------------------------------------------------- conflicts


@dataclass(frozen=True)
class ItemView:
    id: uuid.UUID
    event_id: uuid.UUID
    title: str
    kind: str
    start_at: datetime
    end_at: datetime
    location: str
    vendor_id: uuid.UUID | None


@dataclass(frozen=True)
class EventWindow:
    start_at: datetime
    end_at: datetime
    name: str


@dataclass
class Conflict:
    kind: str
    severity: str  # error | warning
    message: str
    item_ids: list[uuid.UUID]
    suggestion: dict[str, Any] | None = field(default=None)


def _overlaps(a: ItemView, b: ItemView) -> bool:
    return a.start_at < b.end_at and b.start_at < a.end_at


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def _shift(item: ItemView, minutes: int, reason: str) -> dict[str, Any]:
    return {
        "action": "shift_item",
        "item_id": str(item.id),
        "shift_minutes": minutes,
        "reason": reason,
    }


def detect_conflicts(
    items: Sequence[ItemView],
    *,
    events: Mapping[uuid.UUID, EventWindow],
    travel_minutes: Callable[[str, str], int],
    buffer_minutes: int,
) -> list[Conflict]:
    """Find overlaps, vendor double-bookings, impossible travel, missing setup, overruns."""
    conflicts: list[Conflict] = []
    ordered = sorted(items, key=lambda i: (i.start_at, i.end_at))

    # 1. Two main items at the same place at the same time.
    mains = [i for i in ordered if i.kind not in PRE_EVENT_KINDS]
    for idx, a in enumerate(mains):
        for b in mains[idx + 1 :]:
            if b.start_at >= a.end_at:
                break
            if a.location.strip().lower() == b.location.strip().lower() and _overlaps(a, b):
                need = _minutes(a.end_at - b.start_at) + buffer_minutes
                conflicts.append(
                    Conflict(
                        kind="overlap",
                        severity="warning",
                        message=f"'{a.title}' and '{b.title}' overlap at {a.location}.",
                        item_ids=[a.id, b.id],
                        suggestion=_shift(b, need, "start after the earlier item plus buffer"),
                    )
                )

    # 2 + 3. The same vendor in two places at once, or without time to travel between them.
    by_vendor: dict[uuid.UUID, list[ItemView]] = {}
    for item in ordered:
        if item.vendor_id is not None:
            by_vendor.setdefault(item.vendor_id, []).append(item)
    for vendor_items in by_vendor.values():
        for a, b in pairwise(vendor_items):
            same_place = a.location.strip().lower() == b.location.strip().lower()
            if same_place and a.event_id == b.event_id:
                # One vendor covering consecutive items of the same event is normal.
                continue
            if _overlaps(a, b):
                need = _minutes(a.end_at - b.start_at)
                if not same_place:
                    need += travel_minutes(a.location, b.location)
                conflicts.append(
                    Conflict(
                        kind="vendor_double_booked",
                        severity="error",
                        message=f"Vendor booked for '{a.title}' and '{b.title}' at the same time.",
                        item_ids=[a.id, b.id],
                        suggestion=_shift(b, need, "move the later booking after the earlier one"),
                    )
                )
                continue
            if not same_place:
                travel = travel_minutes(a.location, b.location)
                gap = _minutes(b.start_at - a.end_at)
                if gap < travel:
                    conflicts.append(
                        Conflict(
                            kind="travel_time",
                            severity="error",
                            message=(
                                f"Vendor needs ~{travel} min to travel from {a.location} to "
                                f"{b.location} but has only {gap} min."
                            ),
                            item_ids=[a.id, b.id],
                            suggestion=_shift(b, travel - gap, "allow travel time"),
                        )
                    )

    # 4 + 5. Per event: setup must exist and finish before the first main item; no overruns.
    by_event: dict[uuid.UUID, list[ItemView]] = {}
    for item in ordered:
        by_event.setdefault(item.event_id, []).append(item)
    for event_id, window in events.items():
        ev_items = by_event.get(event_id, [])
        if not ev_items:
            continue
        setups = [i for i in ev_items if i.kind == "setup"]
        ev_mains = [i for i in ev_items if i.kind not in PRE_EVENT_KINDS]
        if not setups:
            conflicts.append(
                Conflict(
                    kind="missing_setup",
                    severity="warning",
                    message=f"{window.name} has no setup slot before guests arrive.",
                    item_ids=[i.id for i in ev_mains[:1]],
                )
            )
        elif ev_mains:
            first_main = ev_mains[0]
            late = [s for s in setups if s.end_at > first_main.start_at]
            for s in late:
                conflicts.append(
                    Conflict(
                        kind="setup_overlap",
                        severity="warning",
                        message=f"'{s.title}' is still running when '{first_main.title}' starts.",
                        item_ids=[s.id, first_main.id],
                        suggestion=_shift(
                            s, -_minutes(s.end_at - first_main.start_at), "finish setup earlier"
                        ),
                    )
                )
        for item in ev_mains:
            if item.end_at > window.end_at:
                conflicts.append(
                    Conflict(
                        kind="overrun",
                        severity="warning",
                        message=(
                            f"'{item.title}' ends after {window.name} is scheduled to finish "
                            f"({_minutes(item.end_at - window.end_at)} min over)."
                        ),
                        item_ids=[item.id],
                    )
                )
    return conflicts

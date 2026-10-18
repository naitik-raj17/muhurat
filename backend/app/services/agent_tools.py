"""Every tool an agent may call, registered with its tier.

| Tier | Tools |
|---|---|
| 1 | send_rsvp_reminders, update_guest_status, generate_timeline_draft, suggest_vendors, send_budget_alert |
| 2 | reschedule_vendor_slot, shift_timeline_item (<= 48 h), accept_price_change (<= 5 %) |
| 3 | cancel_contract, modify_contract_terms, execute_payment (registered only to be blocked) |

Executors reuse the normal services, so an agent can never do more than the API allows.
Reversible tools record before/after state, and their undo refuses to run when the target has
changed since (a person edited it), instead of overwriting that edit.
"""  # noqa: E501

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.db.models import GuestRsvp, TimelineItem, VendorShortlist
from app.domain.autonomy import Facts, LimitKind, ToolSpec
from app.domain.enums import RsvpStatus, ShortlistStatus, Tier, TimelineItemStatus, VendorCategory
from app.domain.errors import Conflict, ValidationFailed
from app.domain.schemas import ShortlistAdd, TimelineDraftRequest, VendorSearchQuery
from app.repositories.repos import (
    BudgetRepository,
    EventRepository,
    GuestRepository,
    RsvpRepository,
    ShortlistRepository,
    TimelineRepository,
    WeddingRepository,
)
from app.services import (
    budget_service,
    messaging_service,
    notification_service,
    timeline_service,
    vendor_service,
)
from app.services.action_policy import RegisteredTool, ToolContext, ToolResult, register

GUEST, TIMELINE, VENDOR, BUDGET = "guest", "timeline", "vendor", "budget"
IST = ZoneInfo("Asia/Kolkata")

# Default share of the total budget per category, used to bound vendor suggestions.
CATEGORY_BUDGET_SHARE: dict[str, float] = {
    VendorCategory.VENUE: 0.30,
    VendorCategory.CATERER: 0.25,
    VendorCategory.DECORATOR: 0.12,
    VendorCategory.PHOTOGRAPHER: 0.08,
    VendorCategory.MAKEUP: 0.03,
    VendorCategory.MEHENDI_ARTIST: 0.02,
    VendorCategory.DJ: 0.03,
    VendorCategory.PANDIT: 0.01,
}


class NoArgs(BaseModel):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


# ============================================================================ guest (Tier 1)


async def _send_reminders(ctx: ToolContext, _: NoArgs) -> ToolResult:
    result = await messaging_service.run_reminders(ctx.session, ctx.wedding_id)
    return ToolResult(output=result.model_dump(mode="json"))


register(
    RegisteredTool(
        spec=ToolSpec(
            name="send_rsvp_reminders",
            agent=GUEST,
            tier=Tier.TIER1,
            description="queue RSVP reminders for guests who have not replied",
        ),
        args=NoArgs,
        execute=_send_reminders,
    )
)


class GuestStatusArgs(BaseModel):
    guest_id: uuid.UUID
    status: RsvpStatus
    plus_ones: int = Field(default=0, ge=0, le=20)
    # One event, or every still-pending event of the guest when omitted.
    event_id: uuid.UUID | None = None


async def _update_guest_status(ctx: ToolContext, a: GuestStatusArgs) -> ToolResult:
    guest = await GuestRepository(ctx.session).get(a.guest_id)
    if guest.wedding_id != ctx.wedding_id:
        raise ValidationFailed("Guest belongs to another wedding")
    where: list[Any] = [GuestRsvp.guest_id == guest.id]
    where.append(
        GuestRsvp.event_id == a.event_id
        if a.event_id is not None
        else GuestRsvp.status == RsvpStatus.PENDING.value
    )
    rsvps = await RsvpRepository(ctx.session).list(*where)
    if not rsvps:
        raise ValidationFailed("No matching RSVP to update")
    before = {str(r.id): {"status": r.status, "plus_ones": r.plus_ones} for r in rsvps}
    now = datetime.now(UTC)
    for r in rsvps:
        r.status = a.status.value
        r.plus_ones = a.plus_ones if a.status == RsvpStatus.ACCEPTED else 0
        r.responded_at = now
        r.updated_by_actor = ctx.principal.actor_type.value
    after = {str(r.id): {"status": r.status, "plus_ones": r.plus_ones} for r in rsvps}
    await ctx.session.flush()
    return ToolResult(output={"updated": len(rsvps)}, before=before, after=after)


async def _undo_guest_status(
    ctx: ToolContext, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    repo = RsvpRepository(ctx.session)
    rows = await repo.list(GuestRsvp.id.in_([uuid.UUID(k) for k in before]))
    for r in rows:
        if {"status": r.status, "plus_ones": r.plus_ones} != after.get(str(r.id)):
            raise Conflict("The RSVP changed after the agent's update; not undoing it")
    for r in rows:
        r.status = before[str(r.id)]["status"]
        r.plus_ones = before[str(r.id)]["plus_ones"]
        r.updated_by_actor = "human"
    await ctx.session.flush()
    return {"restored": len(rows)}


register(
    RegisteredTool(
        spec=ToolSpec(
            name="update_guest_status",
            agent=GUEST,
            tier=Tier.TIER1,
            description="update a guest's RSVP status",
            reversible=True,
        ),
        args=GuestStatusArgs,
        execute=_update_guest_status,
        undo=_undo_guest_status,
    )
)


# ============================================================================ timeline


class DraftArgs(BaseModel):
    buffer_minutes: int | None = Field(default=None, ge=0, le=240)


async def _draft_timeline(ctx: ToolContext, a: DraftArgs) -> ToolResult:
    if await TimelineRepository(ctx.session).for_wedding(ctx.wedding_id):
        raise Conflict("A timeline already exists; agents never replace one")
    items = await timeline_service.draft_timeline(
        ctx.session,
        ctx.principal,
        ctx.wedding_id,
        TimelineDraftRequest(buffer_minutes=a.buffer_minutes, replace_existing=False),
    )
    return ToolResult(
        output={"items_created": len(items)},
        before={"item_ids": []},
        after={"item_ids": [str(i.id) for i in items]},
    )


async def _undo_draft(
    ctx: ToolContext, _before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    repo = TimelineRepository(ctx.session)
    ids = [uuid.UUID(i) for i in after.get("item_ids", [])]
    items = await repo.list(TimelineItem.id.in_(ids)) if ids else []
    if any(i.status == TimelineItemStatus.LOCKED.value for i in items):
        raise Conflict("Part of the drafted timeline has been locked; not undoing it")
    for i in items:
        await repo.delete(i)
    await ctx.session.flush()
    return {"items_removed": len(items)}


register(
    RegisteredTool(
        spec=ToolSpec(
            name="generate_timeline_draft",
            agent=TIMELINE,
            tier=Tier.TIER1,
            description="generate a draft multi-day timeline from the events and booked vendors",
            reversible=True,
        ),
        args=DraftArgs,
        execute=_draft_timeline,
        undo=_undo_draft,
    )
)


class ShiftArgs(BaseModel):
    item_id: uuid.UUID
    shift_minutes: int = Field(ge=-7 * 24 * 60, le=7 * 24 * 60)
    reason: str = Field(default="", max_length=500)


async def _item(ctx: ToolContext, item_id: uuid.UUID) -> TimelineItem:
    item = await TimelineRepository(ctx.session).get(item_id)
    if item.wedding_id != ctx.wedding_id:
        raise ValidationFailed("Timeline item belongs to another wedding")
    return item


async def _measure_shift(ctx: ToolContext, a: ShiftArgs) -> Facts:
    item = await _item(ctx, a.item_id)
    return Facts(
        shift_hours=a.shift_minutes / 60,
        target_locked=item.status == TimelineItemStatus.LOCKED.value,
    )


def _slot(item: TimelineItem) -> dict[str, Any]:
    return {"item_id": str(item.id), "start_at": _iso(item.start_at), "end_at": _iso(item.end_at)}


async def _shift(ctx: ToolContext, a: ShiftArgs, *, needs_vendor: bool) -> ToolResult:
    item = await _item(ctx, a.item_id)
    if a.shift_minutes == 0:
        raise ValidationFailed("A shift of 0 minutes changes nothing")
    if needs_vendor and item.vendor_id is None:
        raise ValidationFailed("This item has no vendor; use shift_timeline_item")
    before = _slot(item)
    delta = timedelta(minutes=a.shift_minutes)
    item.start_at += delta
    item.end_at += delta
    item.created_by_actor = ctx.principal.actor_type.value
    output: dict[str, Any] = {"title": item.title, "shift_minutes": a.shift_minutes}
    if needs_vendor and item.vendor_id is not None:
        local = item.start_at.astimezone(IST)
        output["vendor_notified"] = await notification_service.notify_vendor(
            ctx.session,
            organization_id=ctx.principal.organization_id,
            vendor_id=item.vendor_id,
            subject=f"Schedule change: {item.title}",
            body=(
                f"'{item.title}' at {item.location} now starts "
                f"{local:%d %b %Y, %H:%M} IST. Reason: {a.reason or 'schedule conflict'}."
            ),
            dedupe_key=f"{ctx.action_id}:reschedule",
        )
    await ctx.session.flush()
    return ToolResult(output=output, before=before, after=_slot(item))


async def _undo_shift(
    ctx: ToolContext, before: dict[str, Any], after: dict[str, Any], *, notify: bool
) -> dict[str, Any]:
    item = await _item(ctx, uuid.UUID(before["item_id"]))
    if _slot(item) != after:
        raise Conflict("The timeline item was changed after the agent moved it; not undoing it")
    item.start_at = datetime.fromisoformat(before["start_at"])
    item.end_at = datetime.fromisoformat(before["end_at"])
    item.created_by_actor = "human"
    out: dict[str, Any] = {"restored": _slot(item)}
    if notify and item.vendor_id is not None:
        local = item.start_at.astimezone(IST)
        out["vendor_notified"] = await notification_service.notify_vendor(
            ctx.session,
            organization_id=ctx.principal.organization_id,
            vendor_id=item.vendor_id,
            subject=f"Schedule change reverted: {item.title}",
            body=f"'{item.title}' is back to its original time: {local:%d %b %Y, %H:%M} IST.",
            dedupe_key=f"{ctx.action_id}:revert",
        )
    await ctx.session.flush()
    return out


async def _reschedule_vendor(ctx: ToolContext, a: ShiftArgs) -> ToolResult:
    return await _shift(ctx, a, needs_vendor=True)


async def _shift_item(ctx: ToolContext, a: ShiftArgs) -> ToolResult:
    return await _shift(ctx, a, needs_vendor=False)


async def _undo_reschedule(
    ctx: ToolContext, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    return await _undo_shift(ctx, before, after, notify=True)


async def _undo_shift_item(
    ctx: ToolContext, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    return await _undo_shift(ctx, before, after, notify=False)


register(
    RegisteredTool(
        spec=ToolSpec(
            name="reschedule_vendor_slot",
            agent=TIMELINE,
            tier=Tier.TIER2,
            description="move a vendor's slot and notify the vendor",
            reversible=True,
            limit=LimitKind.RESCHEDULE,
        ),
        args=ShiftArgs,
        execute=_reschedule_vendor,
        measure=_measure_shift,
        undo=_undo_reschedule,
    )
)
register(
    RegisteredTool(
        spec=ToolSpec(
            name="shift_timeline_item",
            agent=TIMELINE,
            tier=Tier.TIER2,
            description="adjust the timeline by moving an item",
            reversible=True,
            limit=LimitKind.RESCHEDULE,
        ),
        args=ShiftArgs,
        execute=_shift_item,
        measure=_measure_shift,
        undo=_undo_shift_item,
    )
)


# ============================================================================ vendor


class SuggestArgs(BaseModel):
    category: VendorCategory
    limit: int = Field(default=3, ge=1, le=10)


async def _suggest_vendors(ctx: ToolContext, a: SuggestArgs) -> ToolResult:
    wedding = await WeddingRepository(ctx.session).get(ctx.wedding_id)
    events = await EventRepository(ctx.session).for_wedding(ctx.wedding_id)
    share = CATEGORY_BUDGET_SHARE.get(a.category, 0.05)
    query = VendorSearchQuery(
        category=a.category,
        city=wedding.city,
        budget_max=(wedding.total_budget * Decimal(str(share))).quantize(Decimal(1)),
        event_date=events[0].event_date if events else wedding.start_date,
        styles=list(wedding.styles),
        limit=20,
    )
    found = await vendor_service.search(ctx.session, query)
    over_budget = False
    if not found.results:
        # Nothing fits the default share of the budget: show the best matches anyway and say
        # so, rather than suggesting nothing (the planner may rebalance the budget).
        found = await vendor_service.search(
            ctx.session, query.model_copy(update={"budget_max": None})
        )
        over_budget = True
    already = {
        e.vendor_id
        for e in await ShortlistRepository(ctx.session).list(
            VendorShortlist.wedding_id == ctx.wedding_id
        )
    }
    added: list[str] = []
    for match in found.results:
        if len(added) >= a.limit:
            break
        if match.vendor.id in already:
            continue
        entry = await vendor_service.add_to_shortlist(
            ctx.session,
            ctx.principal,
            ctx.wedding_id,
            ShortlistAdd(
                vendor_id=match.vendor.id,
                score=match.score,
                explanation=match.explanation.model_dump(mode="json"),
                notes=(
                    ("Over the default category budget. " if over_budget else "")
                    + f"Suggested by the vendor agent: {match.explanation.summary}"
                )[:2000],
            ),
        )
        added.append(str(entry.id))
    return ToolResult(
        output={
            "suggested": len(added),
            "search_mode": found.mode,
            "budget_max": query.budget_max,
            "over_budget_fallback": over_budget,
        },
        before={"entry_ids": []},
        after={"entry_ids": added},
    )


async def _undo_suggest(
    ctx: ToolContext, _before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    repo = ShortlistRepository(ctx.session)
    ids = [uuid.UUID(i) for i in after.get("entry_ids", [])]
    entries = await repo.list(VendorShortlist.id.in_(ids)) if ids else []
    removed = kept = 0
    for e in entries:
        # A person already acted on it (shortlisted / selected / rejected): leave it alone.
        if e.status == ShortlistStatus.SUGGESTED.value:
            await repo.delete(e)
            removed += 1
        else:
            kept += 1
    await ctx.session.flush()
    return {"removed": removed, "kept_decided_by_person": kept}


register(
    RegisteredTool(
        spec=ToolSpec(
            name="suggest_vendors",
            agent=VENDOR,
            tier=Tier.TIER1,
            description="suggest vendors for a category (a person still decides)",
            reversible=True,
        ),
        args=SuggestArgs,
        execute=_suggest_vendors,
        undo=_undo_suggest,
    )
)


class PriceArgs(BaseModel):
    entry_id: uuid.UUID
    new_price: Decimal = Field(gt=0)
    note: str = Field(default="", max_length=500)


async def _entry(ctx: ToolContext, entry_id: uuid.UUID) -> VendorShortlist:
    entry = await ShortlistRepository(ctx.session).get(entry_id)
    if entry.wedding_id != ctx.wedding_id:
        raise ValidationFailed("Shortlist entry belongs to another wedding")
    return entry


async def _measure_price(ctx: ToolContext, a: PriceArgs) -> Facts:
    entry = await _entry(ctx, a.entry_id)
    old = entry.quoted_price
    if old is None or old <= 0:
        return Facts(notes=("no current quote to compare with",))
    return Facts(price_change_fraction=float((a.new_price - old) / old))


async def _accept_price(ctx: ToolContext, a: PriceArgs) -> ToolResult:
    entry = await _entry(ctx, a.entry_id)
    before = {"entry_id": str(entry.id), "quoted_price": entry.quoted_price}
    entry.quoted_price = a.new_price
    if a.note:
        entry.notes = f"{entry.notes}\n[agent] price {a.new_price}: {a.note}".strip()[:2000]
    await ctx.session.flush()
    return ToolResult(
        output={"new_price": a.new_price},
        before=before,
        after={"entry_id": str(entry.id), "quoted_price": entry.quoted_price},
    )


async def _undo_price(
    ctx: ToolContext, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    entry = await _entry(ctx, uuid.UUID(before["entry_id"]))
    current = str(entry.quoted_price) if entry.quoted_price is not None else None
    if current is None or Decimal(current) != Decimal(after["quoted_price"]):
        raise Conflict("The quote changed after the agent accepted it; not undoing it")
    old = before["quoted_price"]
    entry.quoted_price = Decimal(old) if old is not None else None
    await ctx.session.flush()
    return {"restored_price": old}


register(
    RegisteredTool(
        spec=ToolSpec(
            name="accept_price_change",
            agent=VENDOR,
            tier=Tier.TIER2,
            description="accept a vendor's negotiated price change",
            reversible=True,
            limit=LimitKind.PRICE_CHANGE,
        ),
        args=PriceArgs,
        execute=_accept_price,
        measure=_measure_price,
        undo=_undo_price,
    )
)


# ============================================================================ budget (Tier 1)


class BudgetAlertArgs(BaseModel):
    category: str = Field(min_length=1, max_length=30)


async def _budget_alert(ctx: ToolContext, a: BudgetAlertArgs) -> ToolResult:
    from app.core.config import get_policy_config

    wedding = await WeddingRepository(ctx.session).get(ctx.wedding_id)
    items = await BudgetRepository(ctx.session).for_wedding(ctx.wedding_id)
    threshold = get_policy_config().budget.alert_threshold_fraction
    status = next(
        (c for c in budget_service.category_status(items, threshold) if c.category == a.category),
        None,
    )
    if status is None or not status.alert:
        raise ValidationFailed(f"'{a.category}' is not over the alert threshold")
    used = f"{status.used_fraction:.0%}" if status.used_fraction is not None else "unplanned"
    queued = await notification_service.notify_staff(
        ctx.session,
        organization_id=ctx.principal.organization_id,
        subject=f"Budget alert: {wedding.name} - {a.category}",
        body=(
            f"{a.category} has used {used} of its plan "
            f"(Rs {status.actual:,.0f} of Rs {status.planned:,.0f})."
        ),
        # Once per category per day, however often the agent runs.
        dedupe_key=f"budget:{ctx.wedding_id}:{a.category}:{datetime.now(UTC):%Y-%m-%d}",
    )
    return ToolResult(output={"queued": queued, "used": used})


register(
    RegisteredTool(
        spec=ToolSpec(
            name="send_budget_alert",
            agent=BUDGET,
            tier=Tier.TIER1,
            description="alert the planners that a budget category passed its threshold",
        ),
        args=BudgetAlertArgs,
        execute=_budget_alert,
    )
)


# ============================================================================ Tier 3 (blocked)


class AnyArgs(BaseModel):
    model_config = {"extra": "allow"}


for _name, _agent, _desc in (
    ("cancel_contract", VENDOR, "cancel a vendor contract"),
    ("modify_contract_terms", VENDOR, "modify the legal terms of a contract"),
    ("execute_payment", BUDGET, "execute a payment"),
):
    register(
        RegisteredTool(
            spec=ToolSpec(name=_name, agent=_agent, tier=Tier.TIER3, description=_desc),
            args=AnyArgs,
        )
    )

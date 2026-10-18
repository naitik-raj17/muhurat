"""Orchestrator: holds the wedding state, dispatches to the four agents, records milestones.

One run = snapshot -> every selected agent plans -> each proposal goes through the
ActionPolicy -> milestone checkpoints are updated. All actions of a run share the run's
correlation ID, which is how the run's result (including blocked attempts, which are written
in their own transaction) is collected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.factory import get_travel
from app.agents.agents import AGENTS, VendorAgent
from app.agents.base import WeddingState
from app.core.config import get_policy_config
from app.core.context import get_request_id
from app.core.logging import get_logger
from app.db.models import AgentAction, ApprovalRequest, GuestRsvp, Vendor, VendorShortlist
from app.domain.errors import PolicyViolation, ValidationFailed
from app.domain.guest_rules import reminder_due
from app.domain.rbac import Principal
from app.domain.schemas import AgentActionOut, AgentRunOut, TimelineItemOut
from app.repositories.repos import (
    AgentActionRepository,
    ApprovalRepository,
    BudgetRepository,
    EventRepository,
    RsvpRepository,
    ShortlistRepository,
    TimelineRepository,
    VendorRepository,
    WeddingRepository,
)
from app.services import action_policy, budget_service, timeline_service

log = get_logger(__name__)


async def build_state(session: AsyncSession, wedding_id: uuid.UUID) -> WeddingState:
    wedding = await WeddingRepository(session).get(wedding_id)
    events = await EventRepository(session).for_wedding(wedding_id)
    items = await TimelineRepository(session).for_wedding(wedding_id)
    conflicts = await timeline_service.conflicts_for(items, events, wedding.city, get_travel())

    now = datetime.now(UTC)
    rules = get_policy_config().reminders
    rsvps = (
        await RsvpRepository(session).list(GuestRsvp.event_id.in_([e.id for e in events]))
        if events
        else []
    )
    invited_pending = due = 0
    for r in rsvps:
        if r.status != "pending" or r.invited_at is None:
            continue
        invited_pending += 1
        if reminder_due(
            status=r.status,
            invited_at=r.invited_at,
            last_reminder_at=r.last_reminder_at,
            reminders_sent=r.reminders_sent,
            now=now,
            days_without_reply=rules.days_without_reply,
            max_reminders=rules.max_reminders,
        ):
            due += 1

    entries = await ShortlistRepository(session).list(VendorShortlist.wedding_id == wedding_id)
    vendors = (
        {
            v.id: v.category
            for v in await VendorRepository(session).list(
                Vendor.id.in_([e.vendor_id for e in entries])
            )
        }
        if entries
        else {}
    )
    shortlist: dict[str, list[str]] = {}
    for e in entries:
        if e.vendor_id in vendors:
            shortlist.setdefault(vendors[e.vendor_id], []).append(e.status)

    budget_items = await BudgetRepository(session).for_wedding(wedding_id)
    threshold = get_policy_config().budget.alert_threshold_fraction
    recent = await AgentActionRepository(session).list(
        AgentAction.wedding_id == wedding_id,
        AgentAction.action.in_(("send_budget_alert", "suggest_vendors")),
        AgentAction.status == "executed",
        AgentAction.created_at >= now - timedelta(hours=24),
    )
    pending = await ApprovalRepository(session).list(
        ApprovalRequest.wedding_id == wedding_id, ApprovalRequest.status == "pending"
    )
    pending_targets = {
        str(p.payload.get("args", {}).get(key))
        for p in pending
        for key in ("item_id", "entry_id")
        if p.payload.get("args", {}).get(key)
    }
    return WeddingState(
        wedding_id=wedding_id,
        city=wedding.city,
        start_date=wedding.start_date,
        milestones=dict(wedding.milestones),
        event_kinds={e.kind for e in events},
        timeline=[TimelineItemOut.model_validate(i) for i in items],
        conflicts=conflicts,
        invited_pending=invited_pending,
        reminders_due=due,
        shortlist=shortlist,
        budget=budget_service.category_status(budget_items, threshold),
        budget_alerted_today={
            str(a.input.get("category")) for a in recent if a.action == "send_budget_alert"
        },
        suggested_recently={
            str(a.input.get("category")) for a in recent if a.action == "suggest_vendors"
        },
        pending_targets=pending_targets,
    )


def compute_milestones(state: WeddingState) -> dict[str, bool]:
    """Checkpoints: vendors booked (every needed category has a selected vendor), invites
    sent, timeline locked."""
    needed = VendorAgent().needed_categories(state)
    return {
        "vendors_booked": all("selected" in state.shortlist.get(c, []) for c in needed),
        "invites_sent": bool(state.milestones.get("invites_sent")),
        "timeline_locked": bool(state.timeline)
        and all(i.status == "locked" for i in state.timeline),
    }


async def run(
    session: AsyncSession,
    principal: Principal,
    wedding_id: uuid.UUID,
    agents: list[str] | None = None,
) -> AgentRunOut:
    unknown = set(agents or []) - set(AGENTS)
    if unknown:
        raise ValidationFailed(f"Unknown agents: {sorted(unknown)}")
    selected = [AGENTS[n] for n in (agents or list(AGENTS))]
    ai = action_policy.as_agent(principal)
    correlation_id = get_request_id()

    state = await build_state(session, wedding_id)
    for agent in selected:
        for call in agent.plan(state):
            try:
                await action_policy.run_tool(
                    session,
                    ai,
                    wedding_id,
                    call.tool,
                    call.args,
                    agent=agent.name,
                    confidence=call.confidence,
                )
            except PolicyViolation as exc:
                log.warning("agent_blocked", agent=agent.name, tool=call.tool, error=exc.detail)

    final = await build_state(session, wedding_id)
    milestones = compute_milestones(final)
    wedding = await WeddingRepository(session).get(wedding_id)
    wedding.milestones = {**wedding.milestones, **milestones}
    await session.flush()

    actions = await AgentActionRepository(session).list(
        AgentAction.wedding_id == wedding_id,
        AgentAction.correlation_id == correlation_id,
        order_by=[AgentAction.created_at],
    )
    summary: dict[str, int] = {}
    for a in actions:
        summary[a.status] = summary.get(a.status, 0) + 1
    log.info("orchestrator_run", wedding_id=str(wedding_id), **summary)
    return AgentRunOut(
        wedding_id=wedding_id,
        correlation_id=correlation_id,
        actions=[AgentActionOut.model_validate(a) for a in actions],
        milestones=wedding.milestones,
        summary=summary,
    )

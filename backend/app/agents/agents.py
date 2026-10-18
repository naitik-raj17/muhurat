"""The four domain agents. Each plans from the wedding snapshot with explicit rules and only
proposes its own registered tools; confidence reflects how certain the rule is."""

from __future__ import annotations

from typing import ClassVar

from app.agents.base import Agent, ProposedCall, WeddingState
from app.domain.enums import VendorCategory

# Conflict kind -> confidence that the suggested shift is the right fix.
_FIX_CONFIDENCE = {
    "vendor_double_booked": 0.9,
    "travel_time": 0.9,
    "setup_overlap": 0.85,
    "overlap": 0.8,
}


class GuestAgent(Agent):
    name = "guest"
    tools = frozenset({"send_rsvp_reminders", "update_guest_status"})

    def plan(self, state: WeddingState) -> list[ProposedCall]:
        if state.reminders_due <= 0:
            return []
        return [
            ProposedCall(
                "send_rsvp_reminders",
                {},
                f"{state.reminders_due} invited guests are due a reminder",
            )
        ]


class TimelineAgent(Agent):
    name = "timeline"
    tools = frozenset({"generate_timeline_draft", "reschedule_vendor_slot", "shift_timeline_item"})

    def plan(self, state: WeddingState) -> list[ProposedCall]:
        if not state.timeline:
            if not state.event_kinds:
                return []
            return [
                ProposedCall(
                    "generate_timeline_draft", {}, "The wedding has events but no timeline yet"
                )
            ]
        items = {str(i.id): i for i in state.timeline}
        calls: list[ProposedCall] = []
        moved: set[str] = set()
        for c in state.conflicts:
            s = c.suggestion
            if not s or s.get("action") != "shift_item":
                continue
            item_id = str(s["item_id"])
            if item_id in moved or item_id in state.pending_targets or item_id not in items:
                continue
            moved.add(item_id)
            tool = (
                "reschedule_vendor_slot"
                if items[item_id].vendor_id is not None
                else "shift_timeline_item"
            )
            calls.append(
                ProposedCall(
                    tool,
                    {
                        "item_id": item_id,
                        "shift_minutes": int(s["shift_minutes"]),
                        "reason": f"{c.kind}: {s.get('reason', '')}",
                    },
                    c.message,
                    confidence=_FIX_CONFIDENCE.get(c.kind, 0.6),
                )
            )
        return calls


class VendorAgent(Agent):
    name = "vendor"
    tools = frozenset(
        {"suggest_vendors", "accept_price_change", "cancel_contract", "modify_contract_terms"}
    )
    _BASE = (
        VendorCategory.VENUE,
        VendorCategory.CATERER,
        VendorCategory.DECORATOR,
        VendorCategory.PHOTOGRAPHER,
        VendorCategory.MAKEUP,
    )
    _BY_EVENT: ClassVar[dict[str, VendorCategory]] = {
        "mehendi": VendorCategory.MEHENDI_ARTIST,
        "sangeet": VendorCategory.DJ,
        "wedding": VendorCategory.PANDIT,
    }

    def needed_categories(self, state: WeddingState) -> list[str]:
        needed = [c.value for c in self._BASE]
        needed += [c.value for kind, c in self._BY_EVENT.items() if kind in state.event_kinds]
        return needed

    def plan(self, state: WeddingState) -> list[ProposedCall]:
        return [
            ProposedCall(
                "suggest_vendors",
                {"category": category, "limit": 3},
                f"No {category} on the shortlist yet",
            )
            for category in self.needed_categories(state)
            if not state.shortlist.get(category) and category not in state.suggested_recently
        ]


class BudgetAgent(Agent):
    name = "budget"
    tools = frozenset({"send_budget_alert", "execute_payment"})

    def plan(self, state: WeddingState) -> list[ProposedCall]:
        return [
            ProposedCall(
                "send_budget_alert",
                {"category": c.category},
                f"{c.category} passed the alert threshold",
            )
            for c in state.budget
            if c.alert and c.category not in state.budget_alerted_today
        ]


AGENTS: dict[str, Agent] = {
    a.name: a for a in (VendorAgent(), GuestAgent(), TimelineAgent(), BudgetAgent())
}

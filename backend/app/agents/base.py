"""Agent base class and the wedding-state snapshot agents plan from.

An agent is a planner, not an executor: `plan(state)` returns proposed tool calls with a
confidence. The orchestrator sends every proposal through the ActionPolicy, which decides
whether it runs, waits for a person, or is blocked. An agent may only propose its own tools.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.schemas import BudgetCategoryStatus, TimelineConflict, TimelineItemOut


@dataclass(frozen=True)
class ProposedCall:
    tool: str
    args: dict[str, Any]
    rationale: str
    confidence: float | None = None


@dataclass
class WeddingState:
    """Read-only snapshot of one wedding, built by the orchestrator."""

    wedding_id: uuid.UUID
    city: str
    start_date: date
    milestones: dict[str, Any]
    event_kinds: set[str]
    timeline: list[TimelineItemOut]
    conflicts: list[TimelineConflict]
    invited_pending: int
    reminders_due: int
    # category -> statuses of the shortlist entries in that category
    shortlist: dict[str, list[str]]
    budget: list[BudgetCategoryStatus]
    budget_alerted_today: set[str] = field(default_factory=set)
    # Categories the vendor agent already searched in the last 24 h (even if nothing matched).
    suggested_recently: set[str] = field(default_factory=set)
    # Targets (item / entry IDs) that already wait for a person: never propose them again.
    pending_targets: set[str] = field(default_factory=set)


class Agent(ABC):
    name: str
    tools: frozenset[str]

    @abstractmethod
    def plan(self, state: WeddingState) -> list[ProposedCall]: ...

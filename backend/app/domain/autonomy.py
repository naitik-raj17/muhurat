"""Autonomy tiers: the pure decision behind the ActionPolicy. No I/O, no framework imports.

Every agent tool is registered with a `ToolSpec`. Before a tool runs, the service layer
measures the concrete call (e.g. how many hours a slot moves) and asks `evaluate` what may
happen:

* Tier 1 (autonomous)  -> execute and log.
* Tier 2 (bounded)     -> execute only within the configured limits; outside them, or when the
                          call cannot be measured, create an approval request that needs an
                          org admin (override). Tier 2 tools must be reversible.
* Tier 3 (prohibited)  -> always blocked for agents, whatever the limits or confidence.

Low confidence always escalates to a person instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.enums import Tier


class LimitKind(StrEnum):
    """Which Tier 2 limit bounds a tool."""

    NONE = "none"
    RESCHEDULE = "reschedule"  # facts: shift_hours
    PRICE_CHANGE = "price_change"  # facts: price_change_fraction


class Decision(StrEnum):
    EXECUTE = "execute"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    agent: str
    tier: Tier
    description: str
    reversible: bool = False
    limit: LimitKind = LimitKind.NONE

    def __post_init__(self) -> None:
        if self.tier == Tier.TIER2 and not self.reversible:
            raise ValueError(f"Tier 2 tool {self.name!r} must be reversible")
        if self.tier == Tier.TIER2 and self.limit == LimitKind.NONE:
            raise ValueError(f"Tier 2 tool {self.name!r} needs a limit")


@dataclass(frozen=True)
class Limits:
    max_reschedule_shift_hours: float
    max_price_change_fraction: float
    min_confidence: float


@dataclass(frozen=True)
class Facts:
    """What the service measured about one concrete call."""

    shift_hours: float | None = None
    price_change_fraction: float | None = None
    # The target was frozen by a person (e.g. a locked timeline item): never change it
    # without asking.
    target_locked: bool = False
    notes: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str
    # True when only an org admin may approve (Tier 2 outside limits).
    requires_override: bool = False


def evaluate(
    spec: ToolSpec, facts: Facts, limits: Limits, confidence: float | None
) -> PolicyDecision:
    if spec.tier == Tier.TIER3:
        return PolicyDecision(
            Decision.BLOCKED,
            f"'{spec.name}' is a Tier 3 action: prohibited for AI agents. Only a person can "
            "do this from the UI.",
        )

    if confidence is not None and confidence < limits.min_confidence:
        return PolicyDecision(
            Decision.NEEDS_APPROVAL,
            f"Confidence {confidence:.2f} is below {limits.min_confidence:.2f}: a person "
            "must decide.",
        )

    if spec.tier == Tier.TIER1:
        return PolicyDecision(Decision.EXECUTE, "Tier 1: autonomous")

    # Tier 2: bounded.
    if facts.target_locked:
        return PolicyDecision(
            Decision.NEEDS_APPROVAL,
            "The target is locked by a person; changing it needs approval.",
            requires_override=True,
        )
    if spec.limit == LimitKind.RESCHEDULE:
        if facts.shift_hours is None:
            return _unmeasurable(spec)
        limit = limits.max_reschedule_shift_hours
        if abs(facts.shift_hours) <= limit:
            return PolicyDecision(
                Decision.EXECUTE,
                f"Tier 2 within limits: shift {abs(facts.shift_hours):.2f} h <= {limit:g} h",
            )
        return PolicyDecision(
            Decision.NEEDS_APPROVAL,
            f"Tier 2 outside limits: shift {abs(facts.shift_hours):.2f} h > {limit:g} h",
            requires_override=True,
        )
    if spec.limit == LimitKind.PRICE_CHANGE:
        if facts.price_change_fraction is None:
            return _unmeasurable(spec)
        limit = limits.max_price_change_fraction
        pct = abs(facts.price_change_fraction)
        if pct <= limit:
            return PolicyDecision(
                Decision.EXECUTE, f"Tier 2 within limits: price change {pct:.1%} <= {limit:.0%}"
            )
        return PolicyDecision(
            Decision.NEEDS_APPROVAL,
            f"Tier 2 outside limits: price change {pct:.1%} > {limit:.0%}",
            requires_override=True,
        )
    return _unmeasurable(spec)  # pragma: no cover - ToolSpec forbids Tier 2 without a limit


def _unmeasurable(spec: ToolSpec) -> PolicyDecision:
    return PolicyDecision(
        Decision.NEEDS_APPROVAL,
        f"Cannot check '{spec.name}' against its limits: a person must decide.",
        requires_override=True,
    )

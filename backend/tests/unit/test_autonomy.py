"""Autonomy tiers: the pure policy decision and the tool registry's invariants."""

from __future__ import annotations

import pytest

from app.agents.agents import AGENTS
from app.domain.autonomy import Decision, Facts, LimitKind, Limits, ToolSpec, evaluate
from app.domain.enums import Tier
from app.services.action_policy import registry

LIMITS = Limits(max_reschedule_shift_hours=48, max_price_change_fraction=0.05, min_confidence=0.7)
T1 = ToolSpec(name="t1", agent="a", tier=Tier.TIER1, description="d")
T2_SHIFT = ToolSpec(
    name="t2", agent="a", tier=Tier.TIER2, description="d", reversible=True,
    limit=LimitKind.RESCHEDULE,
)  # fmt: skip
T2_PRICE = ToolSpec(
    name="t2p", agent="a", tier=Tier.TIER2, description="d", reversible=True,
    limit=LimitKind.PRICE_CHANGE,
)  # fmt: skip
T3 = ToolSpec(name="t3", agent="a", tier=Tier.TIER3, description="d")


@pytest.mark.parametrize("confidence", [None, 0.0, 0.99, 1.0])
def test_tier3_is_always_blocked(confidence: float | None) -> None:
    d = evaluate(T3, Facts(shift_hours=0, price_change_fraction=0), LIMITS, confidence)
    assert d.decision == Decision.BLOCKED


def test_tier1_executes_but_low_confidence_escalates() -> None:
    assert evaluate(T1, Facts(), LIMITS, None).decision == Decision.EXECUTE
    assert evaluate(T1, Facts(), LIMITS, 0.9).decision == Decision.EXECUTE
    low = evaluate(T1, Facts(), LIMITS, 0.5)
    assert low.decision == Decision.NEEDS_APPROVAL and not low.requires_override


@pytest.mark.parametrize(
    ("hours", "decision"),
    [
        (0.5, Decision.EXECUTE),
        (48, Decision.EXECUTE),
        (-48, Decision.EXECUTE),
        (48.01, Decision.NEEDS_APPROVAL),
        (-72, Decision.NEEDS_APPROVAL),
    ],
)
def test_tier2_reschedule_limit(hours: float, decision: Decision) -> None:
    d = evaluate(T2_SHIFT, Facts(shift_hours=hours), LIMITS, 0.95)
    assert d.decision == decision
    if decision == Decision.NEEDS_APPROVAL:
        assert d.requires_override  # outside limits: org admin only


@pytest.mark.parametrize(
    ("fraction", "decision"),
    [
        (0.05, Decision.EXECUTE),
        (-0.05, Decision.EXECUTE),
        (0.0501, Decision.NEEDS_APPROVAL),
        (-0.2, Decision.NEEDS_APPROVAL),
    ],
)
def test_tier2_price_limit(fraction: float, decision: Decision) -> None:
    assert evaluate(T2_PRICE, Facts(price_change_fraction=fraction), LIMITS, None).decision == (
        decision
    )


def test_tier2_unmeasurable_or_locked_needs_override() -> None:
    for facts in (Facts(), Facts(shift_hours=1, target_locked=True)):
        d = evaluate(T2_SHIFT, facts, LIMITS, None)
        assert d.decision == Decision.NEEDS_APPROVAL and d.requires_override


def test_tier2_tools_must_be_reversible_and_bounded() -> None:
    with pytest.raises(ValueError, match="reversible"):
        ToolSpec(name="x", agent="a", tier=Tier.TIER2, description="d", limit=LimitKind.RESCHEDULE)
    with pytest.raises(ValueError, match="limit"):
        ToolSpec(name="x", agent="a", tier=Tier.TIER2, description="d", reversible=True)


def test_registry_invariants() -> None:
    tools = registry()
    tiers = {name: t.spec.tier for name, t in tools.items()}
    assert {"cancel_contract", "modify_contract_terms", "execute_payment"} <= {
        n for n, tier in tiers.items() if tier == Tier.TIER3
    }
    for t in tools.values():
        if t.spec.tier == Tier.TIER3:
            assert t.execute is None  # nothing to run even if the policy were bypassed
        if t.spec.tier == Tier.TIER2:
            assert t.spec.reversible and t.undo is not None and t.measure is not None
    # Every agent only declares tools that exist and belong to it.
    for agent in AGENTS.values():
        for name in agent.tools:
            assert tools[name].spec.agent == agent.name, name

"""ActionPolicy: the single gate every agent action passes through before it runs.

Agents never call services directly. They call `run_tool`, which:

1. looks the tool up in the registry (tools are registered with their tier, see
   `app.services.agent_tools`), refusing unknown tools and tools of another agent;
2. validates the arguments against the tool's Pydantic schema;
3. measures the concrete call (e.g. how far a slot moves) and asks the pure
   `app.domain.autonomy.evaluate` what may happen;
4. executes (Tier 1, Tier 2 within limits), queues an approval request (Tier 2 outside limits,
   low confidence, failed execution) or blocks (Tier 3);
5. writes an `agent_actions` row with input, output and before/after state, so reversible
   actions can be undone from the UI.

Blocked attempts are written in their own transaction, so the audit row survives the error
that is raised to the caller.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_policy_config
from app.core.context import get_request_id
from app.core.logging import get_logger
from app.core.metrics import AGENT_ACTIONS
from app.db.models import AgentAction, ApprovalRequest, User
from app.db.session import get_sessionmaker, scope_session_to_org
from app.domain.autonomy import Decision, Facts, Limits, ToolSpec, evaluate
from app.domain.enums import ActorType, Role, Tier
from app.domain.errors import Conflict, DomainError, Forbidden, NotFound, PolicyViolation
from app.domain.rbac import Permission, Principal
from app.domain.schemas import (
    AgentActionOut,
    ApprovalDecisionOut,
    ApprovalOut,
    ToolInfo,
)
from app.repositories.repos import AgentActionRepository, ApprovalRepository

log = get_logger(__name__)


# --------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    principal: Principal  # always an AI principal acting on behalf of a user
    wedding_id: uuid.UUID
    action_id: uuid.UUID


@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


Executor = Callable[[ToolContext, Any], Awaitable[ToolResult]]
Measurer = Callable[[ToolContext, Any], Awaitable[Facts]]
# Undo gets the recorded before/after state; it must refuse (Conflict) if the target changed
# since, so an undo never silently overwrites a person's later edit.
Undoer = Callable[[ToolContext, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    args: type[BaseModel]
    execute: Executor | None = None
    measure: Measurer | None = None
    undo: Undoer | None = None


_REGISTRY: dict[str, RegisteredTool] = {}


def register(tool: RegisteredTool) -> RegisteredTool:
    spec = tool.spec
    if spec.name in _REGISTRY:
        raise ValueError(f"tool {spec.name!r} registered twice")
    if spec.tier != Tier.TIER3 and tool.execute is None:
        raise ValueError(f"tool {spec.name!r} needs an executor")
    if spec.tier == Tier.TIER3 and tool.execute is not None:
        raise ValueError(f"Tier 3 tool {spec.name!r} must not have an executor")
    if spec.reversible and tool.undo is None:
        raise ValueError(f"reversible tool {spec.name!r} needs an undo")
    _REGISTRY[spec.name] = tool
    return tool


def registry() -> dict[str, RegisteredTool]:
    from app.services import agent_tools  # noqa: F401  (registers every tool)

    return _REGISTRY


def list_tools() -> list[ToolInfo]:
    return [
        ToolInfo(
            name=t.spec.name,
            agent=t.spec.agent,
            tier=t.spec.tier.value,
            description=t.spec.description,
            reversible=t.spec.reversible,
            limit=t.spec.limit.value,
        )
        for t in sorted(registry().values(), key=lambda t: (t.spec.agent, t.spec.name))
    ]


def _limits() -> Limits:
    p = get_policy_config()
    return Limits(
        max_reschedule_shift_hours=p.tier2_limits.max_reschedule_shift_hours,
        max_price_change_fraction=p.tier2_limits.max_price_change_fraction,
        min_confidence=p.agents.min_confidence,
    )


# --------------------------------------------------------------------------- helpers


def jsonable(value: Any) -> Any:
    """JSON-safe copy for audit rows (UUIDs, datetimes and Decimals as strings)."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def as_agent(principal: Principal) -> Principal:
    """The same user, marked as AI: every record the agent writes says it was the AI."""
    return replace(principal, actor_type=ActorType.AI)


async def acting_principal(session: AsyncSession, organization_id: uuid.UUID) -> Principal:
    """AI principal for work nobody clicked (scheduled workflows, inbound replies): it acts on
    behalf of the organization's first active planner (or admin)."""
    rows = (
        await session.execute(
            select(User)
            .where(
                User.organization_id == organization_id,
                User.is_active.is_(True),
                User.role.in_((Role.PLANNER.value, Role.ORG_ADMIN.value)),
            )
            .order_by(User.role.desc(), User.created_at)  # planner sorts before org_admin
        )
    ).scalars()
    user = rows.first()
    if user is None:
        raise NotFound("No active planner or admin to act on behalf of")
    return Principal(
        user_id=user.id,
        organization_id=organization_id,
        role=Role(user.role),
        actor_type=ActorType.AI,
    )


def _record(
    *,
    spec: ToolSpec | None,
    tool: str,
    agent: str,
    wedding_id: uuid.UUID,
    args: dict[str, Any],
    status: str,
    reason: str,
    confidence: float | None,
    action_id: uuid.UUID | None = None,
) -> AgentAction:
    return AgentAction(
        id=action_id or uuid.uuid4(),
        wedding_id=wedding_id,
        agent=agent,
        action=tool,
        tier=spec.tier.value if spec else Tier.TIER3.value,
        actor_type=ActorType.AI.value,
        input=jsonable(args),
        output={},
        status=status,
        reason=reason,
        reversible=bool(spec and spec.reversible),
        confidence=confidence,
        correlation_id=get_request_id(),
    )


async def _persist_blocked(organization_id: uuid.UUID, action: AgentAction) -> None:
    """Separate transaction: the caller's transaction is about to fail with PolicyViolation."""
    async with get_sessionmaker()() as s, s.begin():
        scope_session_to_org(s, organization_id)
        action.organization_id = organization_id
        s.add(action)


# --------------------------------------------------------------------------- run


async def run_tool(
    session: AsyncSession,
    principal: Principal,
    wedding_id: uuid.UUID,
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    agent: str | None = None,
    confidence: float | None = None,
) -> AgentAction:
    """Run one agent tool through the policy. Returns the audit row.

    Raises PolicyViolation (after logging the attempt) for Tier 3 tools, unknown tools and
    tools that belong to another agent.
    """
    if not principal.is_ai:
        raise Forbidden("Tools are for agents; people act through the normal endpoints")
    args = args or {}
    reg = registry().get(tool)
    agent_name = agent or (reg.spec.agent if reg else "unknown")

    def blocked(reason: str) -> AgentAction:
        return _record(
            spec=reg.spec if reg else None,
            tool=tool,
            agent=agent_name,
            wedding_id=wedding_id,
            args=args,
            status="blocked",
            reason=reason,
            confidence=confidence,
        )

    if reg is None or (agent is not None and reg.spec.agent != agent):
        reason = f"Unknown tool '{tool}'" if reg is None else f"'{tool}' is not a tool of {agent}"
        await _persist_blocked(principal.organization_id, blocked(reason))
        AGENT_ACTIONS.labels(agent=agent_name, tier="tier3", status="blocked").inc()
        log.warning("agent_tool_blocked", tool=tool, agent=agent_name, reason=reason)
        raise PolicyViolation(reason)

    spec = reg.spec
    repo = AgentActionRepository(session)
    action_id = uuid.uuid4()
    ctx = ToolContext(session, principal, wedding_id, action_id)

    try:
        parsed = reg.args.model_validate(args)
    except ValidationError as exc:
        action = repo.add(
            _record(
                spec=spec,
                tool=tool,
                agent=agent_name,
                wedding_id=wedding_id,
                args=args,
                status="failed",
                reason=f"Invalid arguments: {exc.errors(include_url=False)}"[:2000],
                confidence=confidence,
                action_id=action_id,
            )
        )
        await session.flush()
        AGENT_ACTIONS.labels(agent=agent_name, tier=spec.tier.value, status="failed").inc()
        return action

    facts = (
        await reg.measure(ctx, parsed)
        if reg.measure is not None and spec.tier != Tier.TIER3
        else Facts()
    )
    decision = evaluate(spec, facts, _limits(), confidence)

    if decision.decision == Decision.BLOCKED:
        await _persist_blocked(principal.organization_id, blocked(decision.reason))
        AGENT_ACTIONS.labels(agent=agent_name, tier=spec.tier.value, status="blocked").inc()
        log.warning("agent_tool_blocked", tool=tool, agent=agent_name, reason=decision.reason)
        raise PolicyViolation(decision.reason, tool=tool, tier=spec.tier.value)

    action = repo.add(
        _record(
            spec=spec,
            tool=tool,
            agent=agent_name,
            wedding_id=wedding_id,
            args=parsed.model_dump(mode="json"),
            status="pending_approval",
            reason=decision.reason,
            confidence=confidence,
            action_id=action_id,
        )
    )
    action.output = {"facts": jsonable(facts.__dict__)} if facts != Facts() else {}

    if decision.decision == Decision.EXECUTE:
        assert reg.execute is not None
        try:
            async with session.begin_nested():
                result = await reg.execute(ctx, parsed)
        except DomainError as exc:
            # Fail-safe: never guess. A person sees the failure in the approvals queue.
            action.status = "pending_approval"
            action.reason = f"Execution failed, escalated to a person: {exc.detail}"
            await _request_approval(session, action, spec, override=False)
        else:
            _apply_result(action, result)
            action.status = "executed"
            action.executed_at = datetime.now(UTC)
    else:
        await _request_approval(session, action, spec, override=decision.requires_override)

    await session.flush()
    AGENT_ACTIONS.labels(agent=agent_name, tier=spec.tier.value, status=action.status).inc()
    log.info(
        "agent_action",
        tool=tool,
        agent=agent_name,
        tier=spec.tier.value,
        status=action.status,
        reason=action.reason,
    )
    return action


def _apply_result(action: AgentAction, result: ToolResult) -> None:
    action.output = {**action.output, **jsonable(result.output)}
    action.before_state = jsonable(result.before) if result.before is not None else None
    action.after_state = jsonable(result.after) if result.after is not None else None


async def _request_approval(
    session: AsyncSession, action: AgentAction, spec: ToolSpec, *, override: bool
) -> None:
    permission = (
        Permission.APPROVAL_DECIDE_OVERRIDE if override else Permission.APPROVAL_DECIDE
    ).value
    ApprovalRepository(session).add(
        ApprovalRequest(
            wedding_id=action.wedding_id,
            agent_action_id=action.id,
            kind=spec.name,
            summary=f"{spec.agent} agent wants to: {spec.description}. {action.reason}",
            payload={"tool": spec.name, "args": action.input, **action.output},
            required_permission=permission,
            status="pending",
        )
    )


# --------------------------------------------------------------------------- decide


def _require_human(principal: Principal, what: str) -> None:
    if principal.is_ai:
        raise PolicyViolation(f"Agents may not {what}")


async def approve(
    session: AsyncSession, principal: Principal, approval_id: uuid.UUID, note: str
) -> ApprovalDecisionOut:
    """A person approves: the action runs now, recorded as AI-proposed, human-approved."""
    _require_human(principal, "approve their own actions")
    approval = await ApprovalRepository(session).get(approval_id)
    if approval.status != "pending":
        raise Conflict(f"Approval request is already {approval.status}")
    needed = Permission(approval.required_permission)
    if not principal.can(needed):
        raise Forbidden(
            "Only an org admin can approve a Tier 2 action outside its limits"
            if needed == Permission.APPROVAL_DECIDE_OVERRIDE
            else f"Your role ({principal.role}) may not approve actions"
        )
    if approval.wedding_id is not None and not principal.can_access_wedding(approval.wedding_id):
        raise NotFound("Approval request not found")
    action = await AgentActionRepository(session).get(approval.agent_action_id)  # type: ignore[arg-type]
    reg = registry().get(action.action)
    if reg is None or reg.spec.tier == Tier.TIER3 or reg.execute is None:
        raise PolicyViolation("This action can never be executed for an agent")

    agent_principal = as_agent(principal)
    assert action.wedding_id is not None
    ctx = ToolContext(session, agent_principal, action.wedding_id, action.id)
    result = await reg.execute(ctx, reg.args.model_validate(action.input))
    _apply_result(action, result)
    now = datetime.now(UTC)
    action.status = "executed"
    action.executed_at = now
    action.approved_by = principal.user_id
    approval.status = "approved"
    approval.decided_by = principal.user_id
    approval.decided_at = now
    approval.decision_note = note
    await session.flush()
    AGENT_ACTIONS.labels(agent=action.agent, tier=action.tier, status="approved").inc()
    return ApprovalDecisionOut(
        approval=ApprovalOut.model_validate(approval),
        action=AgentActionOut.model_validate(action),
    )


async def reject(
    session: AsyncSession, principal: Principal, approval_id: uuid.UUID, note: str
) -> ApprovalDecisionOut:
    _require_human(principal, "reject actions")
    approval = await ApprovalRepository(session).get(approval_id)
    if approval.status != "pending":
        raise Conflict(f"Approval request is already {approval.status}")
    if not principal.can(Permission.APPROVAL_DECIDE):
        raise Forbidden(f"Your role ({principal.role}) may not decide approvals")
    now = datetime.now(UTC)
    approval.status = "rejected"
    approval.decided_by = principal.user_id
    approval.decided_at = now
    approval.decision_note = note
    action = None
    if approval.agent_action_id is not None:
        action = await AgentActionRepository(session).get(approval.agent_action_id)
        action.status = "rejected"
        action.approved_by = None
    await session.flush()
    return ApprovalDecisionOut(
        approval=ApprovalOut.model_validate(approval),
        action=AgentActionOut.model_validate(action) if action else None,
    )


async def rollback(
    session: AsyncSession, principal: Principal, action_id: uuid.UUID, reason: str
) -> AgentActionOut:
    """Undo a reversible executed action from its recorded before/after state."""
    _require_human(principal, "undo actions")
    action = await AgentActionRepository(session).get(action_id)
    if action.wedding_id is not None and not principal.can_access_wedding(action.wedding_id):
        raise NotFound("Agent action not found")
    if action.status == "rolled_back":
        raise Conflict("Action was already undone")
    if action.status != "executed":
        raise Conflict(f"Only executed actions can be undone (this one is {action.status})")
    reg = registry().get(action.action)
    if not action.reversible or reg is None or reg.undo is None:
        raise Conflict("This action is not reversible")
    assert action.wedding_id is not None
    ctx = ToolContext(session, as_agent(principal), action.wedding_id, action.id)
    undo_output = await reg.undo(ctx, action.before_state or {}, action.after_state or {})
    action.status = "rolled_back"
    action.rolled_back_at = datetime.now(UTC)
    action.rolled_back_by = principal.user_id
    action.output = {**action.output, "rollback": jsonable(undo_output), "rollback_reason": reason}
    await session.flush()
    AGENT_ACTIONS.labels(agent=action.agent, tier=action.tier, status="rolled_back").inc()
    return AgentActionOut.model_validate(action)


# --------------------------------------------------------------------------- read


async def list_actions(
    session: AsyncSession,
    *,
    wedding_id: uuid.UUID | None,
    status: str | None,
    limit: int,
) -> list[AgentActionOut]:
    where = []
    if wedding_id is not None:
        where.append(AgentAction.wedding_id == wedding_id)
    if status is not None:
        where.append(AgentAction.status == status)
    rows = await AgentActionRepository(session).list(
        *where, order_by=[AgentAction.created_at.desc()], limit=limit
    )
    return [AgentActionOut.model_validate(r) for r in rows]


async def list_approvals(
    session: AsyncSession,
    *,
    wedding_id: uuid.UUID | None,
    status: str | None,
    limit: int,
) -> list[ApprovalOut]:
    where = []
    if wedding_id is not None:
        where.append(ApprovalRequest.wedding_id == wedding_id)
    if status is not None:
        where.append(ApprovalRequest.status == status)
    rows = await ApprovalRepository(session).list(
        *where, order_by=[ApprovalRequest.created_at.desc()], limit=limit
    )
    return [ApprovalOut.model_validate(r) for r in rows]

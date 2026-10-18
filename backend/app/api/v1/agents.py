"""Agents: run the orchestrator, list tools, activity log with undo, approvals queue."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.agents import orchestrator
from app.api.deps import DbSession, require, require_wedding
from app.domain.rbac import Permission, Principal
from app.domain.schemas import (
    AgentActionOut,
    AgentRunOut,
    AgentRunRequest,
    ApprovalDecisionIn,
    ApprovalDecisionOut,
    ApprovalOut,
    RollbackIn,
    ToolInfo,
)
from app.jobs.worker import kick
from app.services import action_policy

router = APIRouter(tags=["agents"])

AgentRun = Annotated[Principal, Depends(require_wedding(Permission.AGENT_RUN))]
AgentRead = Annotated[Principal, Depends(require(Permission.AGENT_READ))]
AgentRollback = Annotated[Principal, Depends(require(Permission.AGENT_ROLLBACK))]
ApprovalRead = Annotated[Principal, Depends(require(Permission.APPROVAL_READ))]
# The finer check (planner vs. org-admin override) happens in the service, per request.
ApprovalDecide = Annotated[Principal, Depends(require(Permission.APPROVAL_DECIDE))]

ActionStatus = Literal[
    "executed", "blocked", "pending_approval", "rejected", "rolled_back", "failed"
]
ApprovalStatus = Literal["pending", "approved", "rejected"]


@router.post("/weddings/{wedding_id}/agents/run", response_model=AgentRunOut)
async def run_agents(
    wedding_id: uuid.UUID,
    body: AgentRunRequest,
    principal: AgentRun,
    session: DbSession,
    background: BackgroundTasks,
) -> AgentRunOut:
    """Run the orchestrator now. Every proposed action passes through the ActionPolicy."""
    result = await orchestrator.run(session, principal, wedding_id, body.agents or None)
    background.add_task(kick)
    return result


@router.get("/agents/tools", response_model=list[ToolInfo])
async def list_agent_tools(_: AgentRead) -> list[ToolInfo]:
    return action_policy.list_tools()


@router.get("/agent-actions", response_model=list[AgentActionOut])
async def list_agent_actions(
    _: AgentRead,
    session: DbSession,
    wedding_id: uuid.UUID | None = None,
    status: ActionStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AgentActionOut]:
    return await action_policy.list_actions(
        session, wedding_id=wedding_id, status=status, limit=limit
    )


@router.post("/agent-actions/{action_id}/undo", response_model=AgentActionOut)
async def undo_agent_action(
    action_id: uuid.UUID,
    body: RollbackIn,
    principal: AgentRollback,
    session: DbSession,
    background: BackgroundTasks,
) -> AgentActionOut:
    result = await action_policy.rollback(session, principal, action_id, body.reason)
    background.add_task(kick)
    return result


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    _: ApprovalRead,
    session: DbSession,
    wedding_id: uuid.UUID | None = None,
    status: ApprovalStatus | None = "pending",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ApprovalOut]:
    return await action_policy.list_approvals(
        session, wedding_id=wedding_id, status=status, limit=limit
    )


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalDecisionOut)
async def approve_request(
    approval_id: uuid.UUID,
    body: ApprovalDecisionIn,
    principal: ApprovalDecide,
    session: DbSession,
    background: BackgroundTasks,
) -> ApprovalDecisionOut:
    result = await action_policy.approve(session, principal, approval_id, body.note)
    background.add_task(kick)
    return result


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalDecisionOut)
async def reject_request(
    approval_id: uuid.UUID,
    body: ApprovalDecisionIn,
    principal: ApprovalDecide,
    session: DbSession,
) -> ApprovalDecisionOut:
    return await action_policy.reject(session, principal, approval_id, body.note)

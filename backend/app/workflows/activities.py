"""Temporal activities: the I/O behind the workflows. Each opens its own short DB session."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from app.agents import orchestrator
from app.core.config import get_settings
from app.core.context import set_request_id
from app.core.logging import get_logger
from app.db.models import Wedding
from app.db.session import get_sessionmaker, scope_session_to_org
from app.jobs import handlers  # noqa: F401  (registers every outbox handler)
from app.jobs.dispatcher import dispatch_once
from app.services import action_policy
from app.workflows.workflows import (
    DRAIN_OUTBOX,
    ENSURE_WEDDING_WORKFLOWS,
    RUN_WEDDING_AGENTS,
    WeddingInput,
    WeddingWorkflow,
    wedding_workflow_id,
)

log = get_logger(__name__)
INACTIVE_STATUSES = ("completed", "cancelled")


class Activities:
    def __init__(self, client: Client | None) -> None:
        # Only `ensure_wedding_workflows` needs the client (to start workflows).
        self.client = client

    @activity.defn(name=DRAIN_OUTBOX)
    async def drain_outbox(self) -> dict[str, int]:
        sessions = get_sessionmaker()
        total: dict[str, int] = {}
        for _ in range(50):  # bounded: the workflow calls again right after
            outcomes = await dispatch_once(sessions, limit=100)
            if not outcomes:
                break
            for k, v in outcomes.items():
                total[k] = total.get(k, 0) + v
            activity.heartbeat(total)
        if total:
            log.info("outbox_drained", **total)
        return total

    @activity.defn(name=RUN_WEDDING_AGENTS)
    async def run_wedding_agents(self, wedding_id: str, organization_id: str) -> dict[str, Any]:
        """One orchestrator run. `active` is False once the wedding is over or closed."""
        wid, org_id = uuid.UUID(wedding_id), uuid.UUID(organization_id)
        set_request_id(f"wf-{uuid.uuid4().hex[:24]}")
        try:
            async with get_sessionmaker()() as session:
                scope_session_to_org(session, org_id)
                wedding = await session.get(Wedding, wid)
                if wedding is None:
                    return {"active": False, "milestones": {}, "summary": {}}
                if wedding.status in INACTIVE_STATUSES or wedding.end_date < date.today():
                    return {"active": False, "milestones": dict(wedding.milestones), "summary": {}}
                principal = await action_policy.acting_principal(session, org_id)
                result = await orchestrator.run(session, principal, wid)
                await session.commit()
            return {
                "active": True,
                "milestones": result.milestones,
                "summary": result.summary,
                "correlation_id": result.correlation_id,
            }
        finally:
            set_request_id(None)

    @activity.defn(name=ENSURE_WEDDING_WORKFLOWS)
    async def ensure_wedding_workflows(self) -> int:
        assert self.client is not None
        async with get_sessionmaker()() as session:
            rows = (
                await session.execute(
                    select(Wedding.id, Wedding.organization_id).where(
                        Wedding.end_date >= date.today(),
                        Wedding.status.not_in(INACTIVE_STATUSES),
                    )
                )
            ).all()
        for wedding_id, org_id in rows:
            await start_wedding_workflow(self.client, str(wedding_id), str(org_id))
        return len(rows)


async def start_wedding_workflow(
    client: Client, wedding_id: str, organization_id: str, *, nudge: bool = False
) -> None:
    """Idempotent: an already running workflow is reused (and nudged if asked)."""
    settings = get_settings()
    await client.start_workflow(
        WeddingWorkflow.run,
        WeddingInput(
            wedding_id=wedding_id,
            organization_id=organization_id,
            interval_minutes=settings.wedding_workflow_interval_minutes,
        ),
        id=wedding_workflow_id(wedding_id),
        task_queue=settings.temporal_task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        start_signal="nudge" if nudge else None,
    )

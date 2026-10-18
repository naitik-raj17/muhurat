"""Temporal workflows against Temporal's time-skipping test server, with the real activities
running against the test database. Skipped if the test server cannot be started (it is
downloaded on first use)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from temporalio.client import WorkflowHandle
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.core.config import get_policy_config
from app.db.models import OutboxEvent, Wedding
from app.db.session import get_sessionmaker
from app.services import notification_service
from app.workflows.activities import Activities
from app.workflows.workflows import (
    WORKFLOWS,
    OutboxDrainInput,
    OutboxDrainWorkflow,
    WeddingInput,
    WeddingWorkflow,
    wedding_workflow_id,
)
from tests.conftest import agent_session

TASK_QUEUE = "test-wedding-workflows"


@pytest.fixture(autouse=True)
def _daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    m = get_policy_config().messaging
    monkeypatch.setattr(m, "quiet_hours_start", "00:00")
    monkeypatch.setattr(m, "quiet_hours_end", "00:00")


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    try:
        environment = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # offline and not cached yet
        pytest.skip(f"Temporal test server unavailable: {exc}")
    async with environment:
        acts = Activities(environment.client)
        async with Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=WORKFLOWS,
            activities=[acts.drain_outbox, acts.run_wedding_agents, acts.ensure_wedding_workflows],
        ):
            yield environment


async def _eventually(check: Callable[[], Any], within: float = 30.0) -> Any:
    deadline = asyncio.get_running_loop().time() + within
    while True:
        value = await check()
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.2)


async def _runs(handle: WorkflowHandle[Any, Any]) -> int:
    status: dict[str, Any] = await handle.query(WeddingWorkflow.status)
    return int(status["runs"])


async def test_wedding_workflow_runs_agents_on_schedule_and_on_nudge_then_finishes(
    env: WorkflowEnvironment,
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    fresh_wedding: str,
) -> None:
    me = (await client.get("/api/v1/auth/me", headers=planner_a)).json()
    handle = await env.client.start_workflow(
        WeddingWorkflow.run,
        WeddingInput(fresh_wedding, me["organization_id"], interval_minutes=60),
        id=wedding_workflow_id(fresh_wedding),
        task_queue=TASK_QUEUE,
    )
    await _eventually(lambda: _runs_at_least(handle, 1))

    # The first run drafted the timeline and suggested vendors, as the AI, in a workflow run.
    actions = (
        await client.get(
            "/api/v1/agent-actions", params={"wedding_id": fresh_wedding}, headers=planner_a
        )
    ).json()
    assert {"generate_timeline_draft", "suggest_vendors"} <= {a["action"] for a in actions}
    assert all(a["correlation_id"].startswith("wf-") for a in actions)

    # Timer: skipping an hour of workflow time runs the agents again without any signal.
    await env.sleep(timedelta(minutes=61))
    await _eventually(lambda: _runs_at_least(handle, 2))

    # Signal: a nudge runs them right away.
    await handle.signal(WeddingWorkflow.nudge)
    await _eventually(lambda: _runs_at_least(handle, 3))
    checkpoints: dict[str, str] = await handle.query(WeddingWorkflow.checkpoints)
    assert "vendors_booked" not in checkpoints  # nobody selected vendors yet

    # Once the wedding is closed, the next run ends the workflow.
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            update(Wedding).where(Wedding.id == uuid.UUID(fresh_wedding)).values(status="completed")
        )
    await handle.signal(WeddingWorkflow.nudge)
    result = await handle.result()
    assert isinstance(result, dict)


async def _runs_at_least(handle: WorkflowHandle[Any, Any], n: int) -> bool:
    return await _runs(handle) >= n


async def test_outbox_drain_workflow_delivers_and_wakes_on_kick(
    env: WorkflowEnvironment, client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    key = f"test-drain-{uuid.uuid4().hex}"
    async with agent_session(client, planner_a) as (session, ai):
        queued = await notification_service.notify_staff(
            session,
            organization_id=ai.organization_id,
            subject="Drain test",
            body="Delivered by Temporal",
            dedupe_key=key,
        )
    assert queued

    handle = await env.client.start_workflow(
        OutboxDrainWorkflow.run,
        OutboxDrainInput(poll_seconds=3600),  # only the kick can wake it quickly
        id=f"test-outbox-drain-{uuid.uuid4().hex[:8]}",
        task_queue=TASK_QUEUE,
    )
    await handle.signal(OutboxDrainWorkflow.kick)

    async def sent() -> bool:
        async with get_sessionmaker()() as s:
            status = (
                await s.execute(
                    select(OutboxEvent.status).where(OutboxEvent.dedupe_key == f"notify:{key}")
                )
            ).scalar_one()
        return status == "sent"

    await _eventually(sent)
    await handle.terminate("test done")


async def test_supervisor_activity_starts_a_workflow_per_active_wedding(
    env: WorkflowEnvironment, client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    started = await Activities(env.client).ensure_wedding_workflows()
    assert started >= 3  # the seeded weddings are all in the future
    weddings = (await client.get("/api/v1/weddings", headers=planner_a)).json()
    handle = env.client.get_workflow_handle(wedding_workflow_id(weddings[0]["id"]))
    description = await handle.describe()
    assert description.workflow_type == "WeddingWorkflow"
    # Idempotent: running it again reuses the existing workflows.
    assert await Activities(env.client).ensure_wedding_workflows() == started
    # They were started on the production task queue, which this test worker does not poll,
    # so they never ran against the shared seed data. Clean up whatever is still running.
    for w in weddings:
        with contextlib.suppress(RPCError):
            await env.client.get_workflow_handle(wedding_workflow_id(w["id"])).terminate("done")

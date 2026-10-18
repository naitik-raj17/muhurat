"""Temporal worker process: `python -m app.workflows.worker` (the `worker` compose service).

Starts the two singleton workflows (outbox drain, wedding supervisor) if they are not running,
then serves workflow and activity tasks until stopped.
"""

from __future__ import annotations

import asyncio

from prometheus_client import start_http_server
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.worker import Worker

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.workflows.activities import Activities
from app.workflows.client import connect
from app.workflows.workflows import (
    OUTBOX_WORKFLOW_ID,
    SUPERVISOR_WORKFLOW_ID,
    WORKFLOWS,
    OutboxDrainInput,
    OutboxDrainWorkflow,
    SupervisorInput,
    WeddingSupervisorWorkflow,
)

log = get_logger(__name__)


def build_worker(client: Client, task_queue: str) -> Worker:
    acts = Activities(client)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=WORKFLOWS,
        activities=[acts.drain_outbox, acts.run_wedding_agents, acts.ensure_wedding_workflows],
    )


async def start_singletons(client: Client, task_queue: str) -> None:
    settings = get_settings()
    await client.start_workflow(
        OutboxDrainWorkflow.run,
        OutboxDrainInput(poll_seconds=max(settings.outbox_poll_seconds, 1.0)),
        id=OUTBOX_WORKFLOW_ID,
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    await client.start_workflow(
        WeddingSupervisorWorkflow.run,
        SupervisorInput(),
        id=SUPERVISOR_WORKFLOW_ID,
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Agent actions and outbox deliveries happen in this process: expose its metrics too.
    start_http_server(settings.worker_metrics_port)
    client = await connect()
    worker = build_worker(client, settings.temporal_task_queue)
    await start_singletons(client, settings.temporal_task_queue)
    log.info("temporal_worker_started", task_queue=settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

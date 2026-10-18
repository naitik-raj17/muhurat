"""The one job system in use (JOB_BACKEND), behind three calls the API makes after a commit.

* inprocess (Phase 2): the in-process outbox worker; wedding workflows don't exist, agents
  run when a person clicks "Run agents".
* temporal (Phase 3): Temporal runs everything scheduled or long-running. The in-process
  worker is not started, so the two systems never run side by side.

Signals are best effort: the outbox drain also polls, and the supervisor workflow starts any
missing wedding workflow, so a lost signal only delays work, never loses it.
"""

from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _temporal() -> bool:
    return get_settings().job_backend == "temporal"


async def kick_outbox() -> None:
    """New outbox events were committed: deliver them now instead of at the next poll."""
    if not _temporal():
        from app.jobs.worker import kick_local

        kick_local()
        return
    try:
        from app.workflows.client import connect
        from app.workflows.workflows import OUTBOX_WORKFLOW_ID, OutboxDrainWorkflow

        client = await connect()
        await client.get_workflow_handle_for(OutboxDrainWorkflow.run, OUTBOX_WORKFLOW_ID).signal(
            OutboxDrainWorkflow.kick
        )
    except Exception as exc:
        log.warning("temporal_kick_failed", error=f"{type(exc).__name__}: {exc}")


async def wedding_changed(
    wedding_id: uuid.UUID, organization_id: uuid.UUID, *, nudge: bool = True
) -> None:
    """Start the wedding's workflow if needed, and ask it to run the agents soon."""
    if not _temporal():
        return
    try:
        from app.workflows.activities import start_wedding_workflow
        from app.workflows.client import connect

        await start_wedding_workflow(
            await connect(), str(wedding_id), str(organization_id), nudge=nudge
        )
    except Exception as exc:
        log.warning("temporal_wedding_signal_failed", error=f"{type(exc).__name__}: {exc}")

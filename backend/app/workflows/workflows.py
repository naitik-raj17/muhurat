"""Temporal workflows (Phase 3). With JOB_BACKEND=temporal these replace the Phase 2
background jobs entirely: the in-process outbox worker does not run.

* `OutboxDrainWorkflow`   - delivers the transactional outbox (invites, bulk sends, reminders,
                            notifications, contract analysis). Polls, and wakes early on a
                            `kick` signal sent after a request commits new events.
* `WeddingWorkflow`       - one long-lived workflow per wedding. Runs the agent orchestrator
                            on a schedule (and on a `nudge` signal), records milestone
                            checkpoints (vendors booked, invites sent, timeline locked) and
                            finishes when the wedding is over.
* `WeddingSupervisorWorkflow` - makes sure every active wedding has its workflow.

Workflow code must be deterministic: it only imports the standard library and temporalio, and
calls activities by name. All I/O happens in `app.workflows.activities`.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

DRAIN_OUTBOX = "drain_outbox"
RUN_WEDDING_AGENTS = "run_wedding_agents"
ENSURE_WEDDING_WORKFLOWS = "ensure_wedding_workflows"

OUTBOX_WORKFLOW_ID = "outbox-drain"
SUPERVISOR_WORKFLOW_ID = "wedding-supervisor"

# Keep event histories small: start a fresh run after this many loop iterations.
_MAX_ITERATIONS = 500

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=5,
)


def wedding_workflow_id(wedding_id: str) -> str:
    return f"wedding-{wedding_id}"


@dataclass
class OutboxDrainInput:
    poll_seconds: float = 5.0


@workflow.defn
class OutboxDrainWorkflow:
    def __init__(self) -> None:
        self._kicked = False

    @workflow.signal
    def kick(self) -> None:
        self._kicked = True

    @workflow.run
    async def run(self, params: OutboxDrainInput) -> None:
        for _ in range(_MAX_ITERATIONS):
            self._kicked = False
            await workflow.execute_activity(
                DRAIN_OUTBOX,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: self._kicked, timeout=timedelta(seconds=params.poll_seconds)
                )
        workflow.continue_as_new(params)


@dataclass
class WeddingInput:
    wedding_id: str
    organization_id: str
    interval_minutes: int = 360
    # Carried across continue-as-new: milestone -> when the workflow first saw it reached.
    checkpoints: dict[str, str] = field(default_factory=dict)
    runs: int = 0


@workflow.defn
class WeddingWorkflow:
    def __init__(self) -> None:
        self._nudged = False
        self._checkpoints: dict[str, str] = {}
        self._runs = 0
        self._last: dict[str, Any] = {}

    @workflow.signal
    def nudge(self) -> None:
        """Run the agents now (e.g. after guests were imported or a vendor was selected)."""
        self._nudged = True

    @workflow.query
    def checkpoints(self) -> dict[str, str]:
        return dict(self._checkpoints)

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"runs": self._runs, "checkpoints": dict(self._checkpoints), "last": self._last}

    @workflow.run
    async def run(self, params: WeddingInput) -> dict[str, str]:
        self._checkpoints = dict(params.checkpoints)
        self._runs = params.runs
        for _ in range(_MAX_ITERATIONS):
            self._nudged = False
            result: dict[str, Any] = await workflow.execute_activity(
                RUN_WEDDING_AGENTS,
                args=[params.wedding_id, params.organization_id],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=_RETRY,
            )
            self._runs += 1
            self._last = result
            for name, reached in result.get("milestones", {}).items():
                if reached and name not in self._checkpoints:
                    self._checkpoints[name] = workflow.now().isoformat()
            if not result.get("active", True):
                return self._checkpoints
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: self._nudged, timeout=timedelta(minutes=params.interval_minutes)
                )
        workflow.continue_as_new(
            WeddingInput(
                wedding_id=params.wedding_id,
                organization_id=params.organization_id,
                interval_minutes=params.interval_minutes,
                checkpoints=self._checkpoints,
                runs=self._runs,
            )
        )


@dataclass
class SupervisorInput:
    interval_minutes: int = 5


@workflow.defn
class WeddingSupervisorWorkflow:
    @workflow.run
    async def run(self, params: SupervisorInput) -> None:
        for _ in range(_MAX_ITERATIONS):
            await workflow.execute_activity(
                ENSURE_WEDDING_WORKFLOWS,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            await workflow.sleep(timedelta(minutes=params.interval_minutes))
        workflow.continue_as_new(params)


WORKFLOWS = [OutboxDrainWorkflow, WeddingWorkflow, WeddingSupervisorWorkflow]

"""In-process outbox worker (JOB_BACKEND=inprocess): polls for due events and can be kicked
right after a commit. Safe to run in several API replicas at once (claims use SKIP LOCKED).
With JOB_BACKEND=temporal it is not started; Temporal's OutboxDrainWorkflow does this job."""

from __future__ import annotations

import asyncio
import contextlib

from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.jobs import handlers  # noqa: F401  (registers every outbox handler)
from app.jobs.dispatcher import dispatch_once

log = get_logger(__name__)


class OutboxWorker:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="outbox-worker")

    def kick(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        sessions = get_sessionmaker()
        while True:
            try:
                outcomes = await dispatch_once(sessions)
                if outcomes:
                    log.info("outbox_dispatched", **outcomes)
                    continue  # more may be due
            except Exception as exc:  # keep the loop alive; the event stays retryable
                log.error("outbox_worker_error", error=f"{type(exc).__name__}: {exc}")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.interval)


_worker: OutboxWorker | None = None


def set_worker(worker: OutboxWorker | None) -> None:
    global _worker
    _worker = worker


def kick_local() -> None:
    if _worker is not None:
        _worker.kick()


async def kick() -> None:
    """Called as a background task after a request committed new outbox events."""
    from app.jobs.backend import kick_outbox

    await kick_outbox()

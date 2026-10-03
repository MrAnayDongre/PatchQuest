"""A worker: claims queued runs, executes them, keeps its lease alive, and recovers runs whose worker died.

The loop is deliberately small. All the hard decisions live elsewhere: *exclusivity* in ``queue.claim`` (one run,
one owner), *whether an interrupted run may continue* in ``plan_resume``, *what is safe to repeat* in the
promotion journal. A worker that loses its lease abandons the run without writing anything (``abandon``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Callable
from datetime import datetime

from patchquest.application.service import TaskService
from patchquest.database import get_db
from patchquest.persistence import identity as ids
from patchquest.runtime import queue
from patchquest.runtime.resume import NotResumable

logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(self, service: TaskService, *, worker_id: str | None = None, lease_s: float = 30.0, poll_s: float = 1.0,
                 heartbeat_every: float | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self.service = service
        self.id = worker_id or default_worker_id()
        self.lease_s, self.poll_s = lease_s, poll_s
        self.heartbeat_every = heartbeat_every or max(0.05, lease_s / 3)
        self._clock = clock

    def _now(self) -> datetime | None:
        return self._clock() if self._clock else None

    async def run_once(self) -> bool:
        """Claim and fully handle at most one run. True if there was work."""
        claim = await asyncio.to_thread(queue.claim, self.id, self.lease_s, now=self._now())
        if claim is None:
            return False
        if claim.kind == "recover":
            await self._recover(claim)
        else:
            await self._execute(claim)
        return True

    async def _recover(self, claim: queue.Claim) -> None:
        """A dead worker's run is now ``interrupted``. If the resume rules say it is safe, queue it again."""
        try:
            self.service.resume(claim.run_id, defer=True)
        except NotResumable as exc:  # needs a person (drift, partial promotion): leave it interrupted, visibly
            with get_db() as conn:
                ids.audit(conn, "worker.recovery_blocked", actor=f"worker:{self.id}", outcome="needs_decision",
                          target=claim.run_id, detail={"category": exc.plan.category.value, "reasons": list(exc.plan.reasons)})
            logger.warning("run %s needs a decision before it can resume: %s", claim.run_id, exc)

    async def _execute(self, claim: queue.Claim) -> None:
        task = self.service.start_claimed(claim.run_id)
        owned = True
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=self.heartbeat_every)
                if task.done():
                    break
                try:
                    await asyncio.to_thread(queue.heartbeat, claim.run_id, self.id, claim.epoch, self.lease_s, now=self._now())
                except queue.LeaseLost:
                    logger.error("worker %s lost its lease on run %s; abandoning it", self.id, claim.run_id)
                    owned = False  # the run is another worker's now: do not touch its lease or state
                    self._abandon(claim.run_id)
                    await asyncio.gather(task, return_exceptions=True)
                    return
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            # Shutting down mid-run. Stop executing but keep the lease: it expires, and another worker recovers
            # the run from its last checkpoint. Releasing it here would strand a run that is still "running".
            self._abandon(claim.run_id)
            await asyncio.gather(task, return_exceptions=True)
            owned = False
            raise
        finally:
            if owned and task.done():
                await asyncio.shield(asyncio.to_thread(queue.release, claim.run_id, self.id, claim.epoch))

    def _abandon(self, run_id: str) -> None:
        machine = self.service._machines.get(run_id)
        if machine is not None:
            machine.abandon()

    async def run_forever(self, stop: asyncio.Event | None = None, *, max_runs: int | None = None) -> int:
        """Poll until ``stop`` is set (or ``max_runs`` have been handled). Returns the number of runs handled."""
        stop = stop or asyncio.Event()
        handled = 0
        while not stop.is_set() and (max_runs is None or handled < max_runs):
            if await self.run_once():
                handled += 1
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_s)
            except TimeoutError:
                pass
        return handled

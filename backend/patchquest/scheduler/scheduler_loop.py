"""Background scheduler loop that runs within the FastAPI process."""

from __future__ import annotations

import asyncio
import logging

from patchquest.scheduler.scheduler import run_due_tasks

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None


async def start_scheduler_loop(poll_interval: int = 30) -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_loop(poll_interval))
    logger.info(f"Scheduler loop started (poll every {poll_interval}s)")


async def stop_scheduler_loop() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
    logger.info("Scheduler loop stopped")


async def _loop(poll_interval: int) -> None:
    while True:
        try:
            run_ids = run_due_tasks()
            if run_ids:
                logger.info(f"Scheduler triggered {len(run_ids)} run(s): {run_ids}")
        except Exception as e:
            err = str(e).lower()
            if "no such table" in err:
                logger.warning("Scheduler paused: database tables missing — run init_db on startup")
            else:
                logger.error(f"Scheduler loop error: {e}")
        await asyncio.sleep(poll_interval)


def is_running() -> bool:
    return _scheduler_task is not None and not _scheduler_task.done()

"""The process-wide workflow engine and the loop that wakes timers, timeouts and child runs."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from patchquest.application import get_service
from patchquest.plugins import get_host
from patchquest.workflows.catalog import LocalActions
from patchquest.workflows.engine import WorkflowEngine

logger = logging.getLogger(__name__)
_engine: WorkflowEngine | None = None
_task: asyncio.Task[None] | None = None


def get_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine(get_service(), LocalActions(plugins=get_host()))
    return _engine


def set_engine(engine: WorkflowEngine | None) -> None:
    global _engine
    _engine = engine


async def _loop(interval: float) -> None:
    while True:
        try:
            await get_engine().tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("workflow tick failed; will retry")  # one bad run must not stop the loop
        await asyncio.sleep(interval)


async def start_loop(interval: float = 1.0) -> None:
    global _task
    if _task is None:
        _task = asyncio.get_running_loop().create_task(_loop(interval))


async def stop_loop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        with suppress(asyncio.CancelledError):
            await _task
        _task = None

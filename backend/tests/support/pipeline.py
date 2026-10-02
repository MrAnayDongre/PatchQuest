"""Drive the real state machine with a scripted model."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.orchestrator.state_machine import RunStateMachine
from tests.support.db import insert_run
from tests.support.repos import TASK


async def run_scripted(repo: Path, responses: dict, *, task: str = TASK, wait_for=None,
                       runtime_mode: str = "local") -> tuple[RunStateMachine, str]:
    """Run ``task`` on ``repo`` with a scripted model. ``wait_for(sm, run_id)`` runs concurrently
    (e.g. an approver or a canceller). Returns ``(state_machine, run_id)``."""
    run_id = str(uuid.uuid4())
    name = f"script-{run_id}"
    ScriptedProvider.register(name, responses)
    insert_run(run_id, task, str(repo))
    sm = RunStateMachine(run_id, str(repo), task, provider="scripted", model=name, runtime_mode=runtime_mode)
    if wait_for:
        await asyncio.gather(sm.execute(), wait_for(sm, run_id))
    else:
        await sm.execute()
    return sm, run_id

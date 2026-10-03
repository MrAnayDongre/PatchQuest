"""Drive the real state machine with a scripted model."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.evaluation.faults import SimulatedCrash, after_event, crash_when
from patchquest.orchestrator.state_machine import RunStateMachine
from tests.support.db import insert_run
from tests.support.repos import TASK


def prepare_run(repo: Path, responses: dict, *, task: str = TASK, runtime_mode: str = "local") -> tuple[RunStateMachine, str]:
    run_id = str(uuid.uuid4())
    name = f"script-{run_id}"
    ScriptedProvider.register(name, responses)
    insert_run(run_id, task, str(repo), provider="scripted", model=name)
    return RunStateMachine(run_id, str(repo), task, provider="scripted", model=name, runtime_mode=runtime_mode), run_id


__all__ = ["SimulatedCrash", "after_event", "crash_run", "crash_when", "prepare_run", "resume_run", "run_scripted"]


async def crash_run(repo: Path, responses: dict, predicate: Callable[[dict], bool], *, task: str = TASK) -> str:
    """Run until ``predicate`` matches an event, kill the run there, then let startup recovery settle it."""
    from patchquest.recovery import recover_interrupted_runs

    sm, run_id = prepare_run(repo, responses, task=task)
    with crash_when(predicate):
        try:
            await sm.execute()
        except SimulatedCrash:
            pass
        else:
            raise AssertionError("the run finished without reaching the crash point")
    recover_interrupted_runs()
    return run_id


async def resume_run(run_id: str, **options: bool):
    """Resume through the service (as the CLI and API do) and wait for the run to settle."""
    from patchquest.application import TaskService

    svc = TaskService()
    plan = svc.resume(run_id, **options)
    await svc._tasks[run_id]
    return plan


async def run_scripted(repo: Path, responses: dict, *, task: str = TASK, wait_for=None,
                       runtime_mode: str = "local") -> tuple[RunStateMachine, str]:
    """Run ``task`` on ``repo`` with a scripted model. ``wait_for(sm, run_id)`` runs concurrently
    (e.g. an approver or a canceller). Returns ``(state_machine, run_id)``."""
    sm, run_id = prepare_run(repo, responses, task=task, runtime_mode=runtime_mode)
    if wait_for:
        await asyncio.gather(sm.execute(), wait_for(sm, run_id))
    else:
        await sm.execute()
    return sm, run_id

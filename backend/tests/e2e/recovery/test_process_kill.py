"""A real OS process is SIGKILLed mid-run; a different process then resumes it from the database.

Everything else in this package simulates the crash in-process. This proves the durable state is genuinely
all that is needed: no memory, no shared interpreter, no cleanup handlers (SIGKILL cannot be caught).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.database import get_db, get_db_path
from patchquest.persistence import checkpoints
from patchquest.recovery import recover_interrupted_runs
from patchquest.runtime.resume import RecoveryCategory, plan_resume
from tests.support import FIX, PLAN, make_calc_repo, resume_run, run_row

BACKEND = Path(__file__).resolve().parents[3]
FIXED = "def add(a, b):\n    return a + b\n"
REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}

CHILD = textwrap.dedent("""
    import asyncio, json, os, signal, sys
    from patchquest.agents.providers_scripted import ScriptedProvider
    from patchquest.database import init_db
    from patchquest.orchestrator.event_bus import event_bus
    from patchquest.orchestrator.state_machine import RunStateMachine
    from tests.support import insert_run

    run_id, repo, kill_on, name = sys.argv[1:5]
    responses = json.loads(sys.argv[5])
    init_db()
    ScriptedProvider.register(name, responses)
    insert_run(run_id, "Fix add() in calc.py so it returns the sum", repo, provider="scripted", model=name)
    real = event_bus.emit

    async def emit(rid, event):
        await real(rid, event)  # the event is already committed to SQLite
        if event["type"] + ":" + str(event.get("phase")) == kill_on:
            os.kill(os.getpid(), signal.SIGKILL)

    event_bus.emit = emit
    asyncio.run(RunStateMachine(run_id, repo, "Fix add() in calc.py so it returns the sum",
                                provider="scripted", model=name).execute())
""")


def _run_child(tmp_path: Path, repo: Path, kill_on: str) -> tuple[str, str, subprocess.CompletedProcess]:
    run_id, name = str(uuid.uuid4()), f"script-{uuid.uuid4()}"
    env = {**os.environ, "PATCHQUEST_DB": str(get_db_path()), "HOME": str(tmp_path / "home"), "PYTHONPATH": str(BACKEND),
           "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PATCHQUEST_API_TOKEN", None)
    (tmp_path / "home").mkdir(exist_ok=True)
    responses = {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]}
    proc = subprocess.run([sys.executable, "-c", CHILD, run_id, str(repo), kill_on, name, json.dumps(responses)],
                          cwd=BACKEND, env=env, capture_output=True, timeout=120, text=True)
    ScriptedProvider.register(name, responses)  # the resuming process needs the same scripted model
    return run_id, name, proc


@pytest.mark.asyncio
@pytest.mark.parametrize("kill_on", ["checkpoint_created:patching", "command_started:None"])
async def test_sigkilled_process_is_resumed_by_another_process(tmp_path, kill_on):
    repo = make_calc_repo(tmp_path / "repo")
    run_id, _, proc = _run_child(tmp_path, repo, kill_on)
    assert proc.returncode == -signal.SIGKILL, proc.stderr[-800:]

    # What the dead process left behind, read by this (different) process:
    assert run_row(run_id)["status"] == "running"  # nobody got to say otherwise
    with get_db() as conn:
        cp, problems = checkpoints.latest_valid(conn, run_id)
    assert cp is not None and not problems and cp.phase in {"patching", "static_checks"}
    assert (repo / "calc.py").read_text().endswith("a - b\n")  # the real repository was never touched

    assert recover_interrupted_runs() == 1
    plan = plan_resume(run_id)
    assert plan.category is RecoveryCategory.SAFE_RESUME

    await resume_run(run_id)
    row = run_row(run_id)
    assert (row["status"], row["outcome"], row["verdict"]) == ("completed", "applied", "passed")
    assert (repo / "calc.py").read_text() == FIXED
    assert row["attempt"] == 2

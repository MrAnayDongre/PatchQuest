"""Horizontal workers: exclusive execution, lease loss, and recovery of a worker that dies mid-run."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db, get_db_path
from patchquest.persistence import identity as ids
from patchquest.persistence import ledger
from patchquest.runtime.worker import Worker
from tests.support import CALC_BUG, FIX, PLAN, make_calc_repo, run_row

BACKEND = Path(__file__).resolve().parents[3]
REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}
FIXED = "def add(a, b):\n    return a + b\n"
TASK = "Fix add() in calc.py so it returns the sum"


@pytest.fixture(autouse=True)
def unattended():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    set_config(cfg)


def queued_run(repo, model, svc=None):
    svc = svc or TaskService()
    run = svc.create_run(repo_path=str(repo), task=TASK, provider="scripted", model=model)
    svc.enqueue(run["id"])
    return run["id"]


def script(model, **extra):
    ScriptedProvider.register(model, {"planner": [PLAN] * 12, "coder": [FIX] * 12, "reviewer": [REVIEW] * 12, **extra})


def trail(run_id):
    with get_db() as conn:
        return [e["payload"]["to"] for e in ledger.read(conn, run_id) if e["type"] == "run_state_changed"]


@pytest.mark.asyncio
async def test_a_worker_executes_a_queued_run_and_releases_its_lease(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    script("w-model")
    run_id = queued_run(repo, "w-model")
    assert await Worker(TaskService(), worker_id="w1", lease_s=30).run_once() is True
    row = run_row(run_id)
    assert (row["status"], row["outcome"], row["verdict"]) == ("completed", "applied", "passed")
    assert (row["lease_owner"], row["lease_expires_at"]) == (None, None)
    assert trail(run_id) == ["queued", "running", "completed"]
    assert (repo / "calc.py").read_text() == FIXED
    assert await Worker(TaskService(), worker_id="w1").run_once() is False  # nothing left


@pytest.mark.asyncio
async def test_a_pool_of_workers_runs_every_run_exactly_once(tmp_path):
    script("pool-model")
    runs = []
    for i in range(6):
        runs.append(queued_run(make_calc_repo(tmp_path / f"r{i}"), "pool-model"))
    workers = [Worker(TaskService(), worker_id=f"w{i}", lease_s=30, poll_s=0.05) for i in range(3)]
    stop = _stop_after(4.0)
    handled = await asyncio.gather(*(w.run_forever(stop) for w in workers))
    assert sum(handled) == 6  # together they handled each run once
    for run_id in runs:
        assert run_row(run_id)["status"] == "completed"
        assert trail(run_id) == ["queued", "running", "completed"]  # started once, by one worker
    with get_db() as conn:
        starters = [conn.execute("SELECT actor FROM run_events WHERE run_id = ? AND status = 'running' AND type = 'run_state_changed'",
                                 (r,)).fetchall() for r in runs]
    assert all(len(s) == 1 for s in starters)


def _stop_after(seconds):
    event = asyncio.Event()
    asyncio.get_running_loop().call_later(seconds, event.set)
    return event


@pytest.mark.asyncio
async def test_a_worker_that_loses_its_lease_goes_quiet_and_leaves_the_run_to_its_new_owner(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    started = asyncio.Event()

    async def slow_planner(_m):
        started.set()
        await asyncio.sleep(30)

    script("slow-model", planner=[slow_planner])
    run_id = queued_run(repo, "slow-model")
    old = Worker(TaskService(), worker_id="old", lease_s=0.6, heartbeat_every=0.1)
    work = asyncio.create_task(old.run_once())
    await started.wait()
    with get_db() as conn:  # the lease expired and another worker took the run (simulated by stealing the lease)
        conn.execute("UPDATE runs SET lease_owner = 'thief', lease_epoch = lease_epoch + 1, lease_expires_at = '2999-01-01T00:00:00+00:00' "
                     "WHERE id = ?", (run_id,))
        events_before = conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run_id,)).fetchone()[0]
    await asyncio.wait_for(work, timeout=10)
    row = run_row(run_id)
    assert row["status"] == "running" and row["lease_owner"] == "thief"  # not cancelled, failed or released by the old worker
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run_id,)).fetchone()[0] == events_before
    assert (repo / "calc.py").read_text() == CALC_BUG


CHILD = textwrap.dedent("""
    import asyncio, json, os, signal, sys
    from patchquest.agents.providers_scripted import ScriptedProvider
    from patchquest.application import TaskService
    from patchquest.database import init_db
    from patchquest.orchestrator.event_bus import event_bus
    from patchquest.runtime.worker import Worker

    run_id, kill_on = sys.argv[1:3]
    init_db()
    real = event_bus.emit

    async def emit(rid, event):
        await real(rid, event)
        if event["type"] + ":" + str(event.get("phase")) == kill_on:
            os.kill(os.getpid(), signal.SIGKILL)  # the worker dies with its lease still held

    event_bus.emit = emit
    ScriptedProvider.register(sys.argv[3], json.loads(sys.argv[4]))
    asyncio.run(Worker(TaskService(), worker_id="doomed", lease_s=1.0, heartbeat_every=0.2).run_once())
""")


def kill_a_worker_mid_run(tmp_path, repo, kill_on="checkpoint_created:patching"):
    model = f"doomed-{uuid.uuid4().hex[:6]}"
    responses = {"planner": [PLAN] * 4, "coder": [FIX] * 4, "reviewer": [REVIEW] * 4}
    ScriptedProvider.register(model, responses)  # the surviving worker needs the same scripted model
    run_id = queued_run(repo, model)
    env = {**os.environ, "PATCHQUEST_DB": str(get_db_path()), "HOME": str(tmp_path / "home"), "PYTHONPATH": str(BACKEND),
           "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PATCHQUEST_API_TOKEN", None)
    (tmp_path / "home").mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, "-c", CHILD, run_id, kill_on, model, json.dumps(responses)], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == -signal.SIGKILL, proc.stderr[-800:]
    return run_id


@pytest.mark.asyncio
async def test_a_sigkilled_worker_is_recovered_by_another_worker_after_its_lease_expires(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    run_id = kill_a_worker_mid_run(tmp_path, repo)
    row = run_row(run_id)
    assert row["status"] == "running" and row["lease_owner"] == "doomed"  # nobody told the database
    assert (repo / "calc.py").read_text() == CALC_BUG  # the real repository was not touched

    survivor = Worker(TaskService(), worker_id="survivor", lease_s=30)
    assert await survivor.run_once() is False  # the dead worker's lease is still valid: hands off
    time.sleep(1.3)  # the lease (1s) expires
    assert await survivor.run_once() is True  # recover: interrupted, then queued under the resume rules
    assert run_row(run_id)["status"] == "queued"
    assert await survivor.run_once() is True  # claim and finish
    final = run_row(run_id)
    assert (final["status"], final["outcome"], final["verdict"], final["attempt"]) == ("completed", "applied", "passed", 2)
    assert trail(run_id) == ["queued", "running", "interrupted", "queued", "running", "completed"]
    assert (repo / "calc.py").read_text() == FIXED
    assert final["lease_owner"] is None


@pytest.mark.asyncio
async def test_recovery_will_not_resume_over_a_humans_edit(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    run_id = kill_a_worker_mid_run(tmp_path, repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a  # fixed by hand while the worker was down\n")
    time.sleep(1.3)
    survivor = Worker(TaskService(), worker_id="survivor", lease_s=30)
    assert await survivor.run_once() is True  # it recovers the run, but only as far as 'interrupted'
    assert run_row(run_id)["status"] == "interrupted"
    assert await survivor.run_once() is False  # and it does not resume it behind anyone's back
    with get_db() as conn:
        audit = [e for e in ids.read_audit(conn, None) if e["action"] == "worker.recovery_blocked"]
    assert audit and audit[0]["detail"]["category"] == "HUMAN_CONFIRMATION_REQUIRED" and audit[0]["target"] == run_id
    assert "fixed by hand" in (repo / "calc.py").read_text()


@pytest.mark.asyncio
async def test_startup_recovery_leaves_leased_runs_to_the_workers(tmp_path):
    from patchquest.recovery import recover_interrupted_runs

    repo = make_calc_repo(tmp_path / "r")
    run_id = kill_a_worker_mid_run(tmp_path, repo)
    assert recover_interrupted_runs() == 0  # an API process restarting must not steal a worker's run
    assert run_row(run_id)["status"] == "running"

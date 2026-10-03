"""The "kill the worker" demonstration, with a real process and a real SIGKILL.

A child process runs a worker that has claimed a run and is partway through it (its model call hangs). The parent
kills it with SIGKILL, waits for the lease to expire, and a second worker recovers the run from its last checkpoint and
finishes it. The repository is written exactly once, and the ledger tells the story. Uses a throwaway SQLite database and
a copy of a demo repository; nothing else is touched.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from patchquest.demo import fixtures as fx

LEASE_S = 2.0
MODEL = "demo-crash"
CHILD = """
import asyncio, sys
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.database import init_db
from patchquest.runtime.worker import Worker

init_db()

async def hang(_messages):          # the model call that never returns: this worker is mid-run
    print("worker-1: planning (model call in flight)", flush=True)
    await asyncio.sleep(3600)

ScriptedProvider.register(%(model)r, {"planner": [hang]})
asyncio.run(Worker(TaskService(), worker_id="worker-1", lease_s=%(lease)s).run_once())
"""


def _say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run() -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="pq-demo-crash-"))
    repo = work / "payments-service"
    fx.write_repo(repo, fx.PAYMENTS)
    env = {**os.environ, "PATCHQUEST_DB": str(work / "crash.db"), "HOME": str(work)}
    os.environ.update({"PATCHQUEST_DB": env["PATCHQUEST_DB"], "HOME": env["HOME"]})
    from patchquest import database
    from patchquest.agents.providers_scripted import ScriptedProvider
    from patchquest.application import TaskService
    from patchquest.config import AppConfig, set_config
    from patchquest.persistence import ledger
    from patchquest.runtime.worker import Worker

    database.use_sqlite()
    database.set_db_path(work / "crash.db")
    set_config(AppConfig(queue_mode=True))
    database.init_db()
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task="Fix net_amount: the merchant is credited the amount plus the fee instead of minus it",
                         provider="scripted", model=MODEL)
    svc.enqueue(run["id"])
    _say(f"run {run['id'][:8]} queued; starting worker-1 in its own process")
    child = subprocess.Popen([sys.executable, "-c", CHILD % {"model": MODEL, "lease": LEASE_S}], env=env, stdout=subprocess.PIPE, text=True)  # noqa: S603
    try:
        if child.stdout is None:
            raise RuntimeError("no pipe to the worker process")
        child.stdout.readline()
        _say("worker-1 holds the lease and is mid-run - killing it with SIGKILL")
        killed_at = time.time()
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
    finally:
        if child.poll() is None:
            child.kill()
    with database.get_db() as conn:
        before = conn.execute("SELECT status, lease_owner FROM runs WHERE id = ?", (run["id"],)).fetchone()
    _say(f"the database still says the run is '{before['status']}' owned by {before['lease_owner']}: nobody has noticed yet")

    ScriptedProvider.register(MODEL, {"planner": [fx.plan(["payments/fees.py"], "net_amount adds the fee", fx.unit("fees"))], "coder": [fx.FIX_NET],
                                      "reviewer": [fx.REVIEW_OK]})
    worker2 = Worker(svc, worker_id="worker-2", lease_s=30, poll_s=0.1)

    async def recover() -> float:
        recovered = None
        while True:
            await worker2.run_once()
            with database.get_db() as conn:
                status = conn.execute("SELECT status FROM runs WHERE id = ?", (run["id"],)).fetchone()["status"]
            if recovered is None and status != "running":
                recovered = time.time()
                _say(f"worker-2: the lease expired {recovered - killed_at:.1f}s after the kill; run recovered as '{status}'")
            if status == "completed":
                return recovered or time.time()
            await asyncio.sleep(0.1)

    asyncio.run(asyncio.wait_for(recover(), 120))
    with database.get_db() as conn:
        final = conn.execute("SELECT status, outcome, verdict FROM runs WHERE id = ?", (run["id"],)).fetchone()
        events = [e for e in ledger.read(conn, run["id"], limit=10_000)]
    story = [e for e in events if e["type"] in ("run_interrupted", "run_resumed", "resume_started", "checkpoint_restored", "promotion_started", "promotion_completed", "patch_applied", "run_completed")]
    for e in story:
        _say(f"ledger: {e['type']:<22} {e['actor']:<10} {(e['message'] or '')[:70]}")
    applied = [e for e in events if e["type"] == "patch_applied"]
    fixed = "amount_cents - compute_fee(amount_cents)" in (repo / "payments" / "fees.py").read_text()
    _say(f"result: {final['status']}/{final['outcome']}/{final['verdict']}; patch applied {len(applied)} time(s); repository fixed: {fixed}")
    shutil.rmtree(work, ignore_errors=True)
    return {"run_id": run["id"], "status": final["status"], "outcome": final["outcome"], "patch_applied_times": len(applied), "repository_fixed": fixed,
            "events": [e["type"] for e in story]}

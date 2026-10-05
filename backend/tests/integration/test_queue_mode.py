"""Queue mode through the API, readiness probes, the worker CLI, and shutdown behaviour."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from patchquest import cli
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.runtime.worker import Worker
from tests.support import FIX, PLAN, make_calc_repo, run_row

REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}


@pytest.fixture(autouse=True)
def queue_mode():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    cfg.queue_mode = True
    set_config(cfg)
    ScriptedProvider.register("q-model", {"planner": [PLAN] * 10, "coder": [FIX] * 10, "reviewer": [REVIEW] * 10})


def client():
    from patchquest.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


@pytest.mark.asyncio
async def test_the_api_only_queues_and_a_worker_does_the_work(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    async with client() as c:
        r = await c.post("/api/runs", json={"repo_path": str(repo), "task": "Fix add() in calc.py so it returns the sum",
                                            "provider": "scripted", "model": "q-model"})
        run_id = r.json()["id"]
        assert r.json()["status"] == "queued"
        await asyncio.sleep(0.2)
        assert run_row(run_id)["status"] == "queued" and (repo / "calc.py").read_text().endswith("a - b\n")  # nothing ran in the API

        assert await Worker(TaskService(), worker_id="w1").run_once() is True
        done = (await c.get(f"/api/runs/{run_id}")).json()
        assert (done["status"], done["outcome"]) == ("completed", "applied")


@pytest.mark.asyncio
async def test_resume_and_fork_are_queued_for_workers_too(tmp_path):
    from tests.support import after_event, crash_run

    repo = make_calc_repo(tmp_path / "r")
    run_id = await crash_run(repo, {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]}, after_event("checkpoint_created", phase="patching"))
    async with client() as c:
        resumed = await c.post(f"/api/runs/{run_id}/resume", json={})
        assert resumed.status_code == 200 and run_row(run_id)["status"] == "queued"
        assert run_row(run_id)["attempt"] == 2
        assert await Worker(TaskService(), worker_id="w1").run_once() is True
        assert run_row(run_id)["status"] == "completed"
        assert (repo / "calc.py").read_text().endswith("a + b\n")

        (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # put the bug back for the fork
        ScriptedProvider.register("alt", {"coder": [FIX] * 3, "reviewer": [REVIEW] * 3})
        fork = await c.post(f"/api/runs/{run_id}/fork", json={"from_checkpoint": 6, "model": "alt"})
        assert fork.status_code == 200 and fork.json()["status"] == "queued"
        assert await Worker(TaskService(), worker_id="w2").run_once() is True
        child = run_row(fork.json()["id"])
        assert child["status"] == "completed" and child["parent_run_id"] == run_id


@pytest.mark.asyncio
async def test_live_and_ready_and_a_failing_ready_says_why():
    async with client() as c:
        assert (await c.get("/live")).json() == {"status": "live"}
        ready = await c.get("/ready")
        assert ready.status_code == 200 and ready.json()["status"] == "ready"
        assert {k: v["ok"] for k, v in ready.json()["checks"].items()} == {"database": True, "schema": True, "workspace_storage": True}
        with get_db() as conn:  # a database from a newer release: this instance must not claim to be ready
            conn.execute("INSERT INTO schema_migrations VALUES (999, 'future', 'n')")
        bad = await c.get("/ready")
        assert bad.status_code == 503 and bad.json()["checks"]["schema"]["ok"] is False and bad.json()["checks"]["schema"]["applied"] == 999


@pytest.mark.asyncio
async def test_probes_work_without_a_token_but_the_api_does_not():
    from patchquest.domain.identity import Role
    from patchquest.persistence import identity as ids

    with get_db() as conn:
        org = ids.create_org(conn, "A")
        ws = ids.create_workspace(conn, org, "w")
        p = ids.create_principal(conn, org, "ana")
        ids.set_role(conn, p, ws, Role.OWNER)
        ids.issue_token(conn, p)
    async with client() as c:
        assert (await c.get("/live")).status_code == 200 and (await c.get("/ready")).status_code == 200
        assert (await c.get("/api/runs")).status_code == 401


@pytest.mark.asyncio
async def test_a_worker_stopped_mid_run_keeps_its_lease_so_another_worker_can_recover_the_run(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    started = asyncio.Event()

    async def slow(_m):
        started.set()
        await asyncio.sleep(60)

    ScriptedProvider.register("slow-q", {"planner": [slow]})
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task="Fix add() in calc.py", provider="scripted", model="slow-q")
    svc.enqueue(run["id"])
    work = asyncio.create_task(Worker(svc, worker_id="w1", lease_s=30).run_once())
    await started.wait()
    work.cancel()  # shutdown past the grace period
    with pytest.raises(asyncio.CancelledError):
        await work
    row = run_row(run["id"])
    assert row["status"] == "running" and row["lease_owner"] == "w1"  # still leased: it will expire and be recovered, not stranded
    await asyncio.sleep(0.2)
    with get_db() as conn:
        types = [e["type"] for e in __import__("patchquest.persistence.ledger", fromlist=["read"]).read(conn, run["id"])]
    assert "run_failed" not in types and "run_interrupted" not in types  # the stopped worker wrote no ending


def test_cli_worker_once_and_queue_stats(tmp_path, capsys):
    repo = make_calc_repo(tmp_path / "r")
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task="Fix add() in calc.py so it returns the sum", provider="scripted", model="q-model")
    svc.enqueue(run["id"])
    assert cli.main(["queue", "--json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["queued"] == 1
    assert cli.main(["worker", "--once", "--id", "cli-worker"]) == cli.EXIT_OK
    assert run_row(run["id"])["status"] == "completed"
    assert cli.main(["queue"]) == cli.EXIT_OK
    assert "queued 0" in capsys.readouterr().out

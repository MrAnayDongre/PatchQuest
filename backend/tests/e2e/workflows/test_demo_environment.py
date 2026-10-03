"""The demo world is built from nothing, is honest about what is simulated, never touches real state, and runs the real pipeline."""

from __future__ import annotations

import os

import pytest

from patchquest import demo
from patchquest.database import get_db
from patchquest.demo import seed, simulators
from patchquest.demo.routes import STATE
from patchquest.integrations import service as integrations
from patchquest.workflows.runtime import get_engine
from tests.support import run_row


@pytest.fixture
async def world(tmp_path, monkeypatch):
    for key in simulators.ENV:
        monkeypatch.delenv(key, raising=False)
    directory = demo.reset(tmp_path / "demo")
    sims = simulators.Simulators()
    STATE["sims"] = sims
    result = await seed.seed(directory, sims)
    yield result, sims, directory
    integrations.set_http(None)
    for key in simulators.ENV:
        os.environ.pop(key, None)


@pytest.mark.asyncio
async def test_the_seeded_history_has_every_kind_of_outcome(world):
    result, _, _ = world
    runs = {k: run_row(v) for k, v in result["runs"].items()}
    assert (runs["fees"]["status"], runs["fees"]["outcome"], runs["fees"]["verdict"]) == ("completed", "applied", "passed")
    assert (runs["cart"]["status"], runs["cart"]["outcome"], runs["cart"]["verdict"]) == ("completed", "applied", "passed")
    assert runs["refund"]["outcome"] == "applied" and runs["refund"]["verdict"] == "passed"  # needed a repair round to get there
    assert runs["explain"]["outcome"] == "read_only" and runs["explain"]["status"] == "completed"
    assert runs["tax_denied"]["outcome"] == "rejected"
    assert runs["tax_pending"]["status"] == "waiting_approval"  # a person has something to do
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ? AND type = 'repair_started'", (result["runs"]["refund"],)).fetchone()[0] >= 1
        pending = conn.execute("SELECT type, reason FROM approvals WHERE run_id = ? AND status = 'pending'", (result["runs"]["tax_pending"],)).fetchone()
    assert (pending["type"] == "promote_patch" and "unresolved" in pending["reason"]) or "validation verdict" in pending["reason"]


@pytest.mark.asyncio
async def test_fixes_really_landed_in_the_demo_repositories_and_nowhere_else(world):
    result, _, directory = world
    fees = (directory / "repos" / "payments-service" / "payments" / "fees.py").read_text()
    assert "amount_cents - compute_fee(amount_cents)" in fees
    assert "item.price * item.quantity" in (directory / "repos" / "web-checkout" / "src" / "cart.js").read_text()
    assert "int(subtotal_cents * rate)" in (directory / "repos" / "payments-service" / "payments" / "tax.py").read_text()  # the denied one did not land
    assert str(directory) in result["repos"]["payments"]


@pytest.mark.asyncio
async def test_integrations_are_labelled_as_simulators_and_workflow_policy_memory_exist(world):
    with get_db() as conn:
        names = {r["kind"]: r["name"] for r in conn.execute("SELECT kind, name FROM integrations")}
        assert names == {"github": "GitHub (simulator)", "slack": "Slack (simulator)"}
        assert conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM policies WHERE name = 'release-safety'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE key = 'refunds.limit'").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_seeding_twice_adds_nothing(world, tmp_path):
    _, sims, directory = world
    with get_db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert (await seed.seed(directory, sims)) == {"seeded": False}
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == before


@pytest.mark.asyncio
async def test_the_live_scenario_issue_to_fix_to_approval_to_github_and_slack(world):
    import asyncio

    import httpx
    from fastapi import FastAPI

    from patchquest.api.routes_hooks import router as hooks_router
    from patchquest.demo import routes

    _, sims, _ = world
    live = FastAPI()  # the demo endpoints are only registered when PATCHQUEST_DEMO=1, so assemble the relevant parts here
    live.include_router(hooks_router)
    live.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=live), base_url="http://localhost") as client:
        triggered = (await client.post("/api/demo/trigger-issue")).json()
        assert triggered["webhook_status"] == 202 and triggered["webhook"]["workflows"] == 1
        with get_db() as conn:
            wf_run = conn.execute("SELECT id FROM workflow_runs").fetchone()["id"]
        engine = get_engine()
        for _ in range(200):
            await engine.tick()
            with get_db() as conn:
                if conn.execute("SELECT 1 FROM workflow_steps WHERE workflow_run_id = ? AND node_id = 'review' AND status = 'waiting'", (wf_run,)).fetchone():
                    break
            await asyncio.sleep(0.05)
        assert sims.transcript() == {"github_comments": [], "slack_messages": []}  # nothing leaves before a person approves
        await engine.decide(wf_run, "review", "approve", "demo:ana")
        for _ in range(100):
            await engine.tick()
            with get_db() as conn:
                if conn.execute("SELECT status FROM workflow_runs WHERE id = ?", (wf_run,)).fetchone()["status"] == "completed":
                    break
            await asyncio.sleep(0.05)
        said = (await client.get("/api/demo/transcript")).json()
    assert len(said["github_comments"]) == 1 and "validated a fix" in said["github_comments"][0].lower()
    assert said["slack_messages"] == [f"Validated a fix for #{triggered['issue']}: Prices like 19.99 are charged as 19.98"]
    with get_db() as conn:  # the fix itself was validated by the repository's own tests before anything was posted
        child = conn.execute("SELECT id, status, outcome, verdict FROM runs WHERE created_by LIKE 'workflow:%'").fetchone()
    assert (child["status"], child["outcome"], child["verdict"]) == ("completed", "applied", "passed")


def test_reset_only_touches_marked_demo_directories(tmp_path, monkeypatch):
    target = demo.reset(tmp_path / "d")
    (target / "x.txt").write_text("x")
    assert demo.reset(target) == target.resolve() and not (target / "x.txt").exists()
    stranger = tmp_path / "mine"
    stranger.mkdir()
    (stranger / "precious.txt").write_text("keep")
    with pytest.raises(RuntimeError, match=r"no \.patchquest-demo marker"):
        demo.reset(stranger)
    assert (stranger / "precious.txt").exists()
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="not a demo directory"):
        demo.reset(tmp_path)
    with pytest.raises(RuntimeError, match="not a demo directory"):
        demo.reset(tmp_path / ".patchquest")


def test_the_kill_the_worker_demo_recovers_with_one_write_and_a_real_sigkill(tmp_path, monkeypatch, capsys):
    """Runs in a subprocess: the demo switches the process-wide database and configuration."""
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "patchquest.cli", "demo", "crash"], capture_output=True, text=True, timeout=180,
                         env={**os.environ, "HOME": str(tmp_path)})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "killing it with SIGKILL" in out.stdout and "run_interrupted" in out.stdout and "patch applied 1 time(s); repository fixed: True" in out.stdout

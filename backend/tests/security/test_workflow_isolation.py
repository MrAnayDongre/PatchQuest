"""Workflows and their runs are tenant-scoped, and roles decide who may define, run and approve."""

from __future__ import annotations

import pytest

from patchquest.application import TaskService
from patchquest.database import get_db
from patchquest.domain.workflows import parse
from patchquest.workflows import store
from patchquest.workflows.catalog import LocalActions
from patchquest.workflows.engine import WorkflowEngine
from patchquest.workflows.runtime import set_engine

DEFINITION = {"name": "gate-flow", "trigger": {"type": "manual"}, "nodes": [
    {"id": "gate", "type": "approval", "config": {}}, {"id": "done", "type": "end"}],
    "edges": [{"from": "gate", "to": "done", "when": "approved"}]}


@pytest.fixture
async def flows(world):
    engine = WorkflowEngine(TaskService(), LocalActions())
    set_engine(engine)
    out = {}
    for name in ("a", "b"):
        with get_db() as conn:
            wf_id, _ = store.save_version(conn, world.ws[name], parse(DEFINITION), "seed")
        run_id = engine.start(wf_id, {"type": "manual"})
        await engine.advance(run_id)
        out[name] = (wf_id, run_id)
    return out


ROUTES = [
    ("GET", "/api/workflows/{wf}", None),
    ("POST", "/api/workflows/{wf}/runs", {}),
    ("GET", "/api/workflows/runs/{run}", None),
    ("POST", "/api/workflows/runs/{run}/steps/gate/decision", {"decision": "approve"}),
    ("POST", "/api/workflows/runs/{run}/steps/gate/resolve", {"outcome": "failed"}),
    ("POST", "/api/workflows/runs/{run}/cancel", None),
]


def state():
    with get_db() as conn:
        return ([tuple(r) for r in conn.execute("SELECT id, status FROM workflow_runs ORDER BY id")],
                [tuple(r) for r in conn.execute("SELECT workflow_run_id, node_id, status FROM workflow_steps ORDER BY id")],
                conn.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES])
async def test_another_tenant_cannot_see_or_touch_a_workflow_or_its_runs(world, flows, method, path, body):
    before = state()
    wf, run = flows["a"]
    async with world.client("owner_b") as c:
        r = await c.request(method, path.format(wf=wf, run=run), json=body)
    assert r.status_code == 404 and state() == before


@pytest.mark.asyncio
async def test_listings_only_show_your_workspaces(world, flows):
    async with world.client("viewer_a") as c:
        assert [w["id"] for w in (await c.get("/api/workflows")).json()] == [flows["a"][0]]
        assert [r["id"] for r in (await c.get("/api/workflows/runs")).json()] == [flows["a"][1]]


@pytest.mark.asyncio
async def test_you_cannot_save_a_workflow_into_someone_elses_workspace(world, flows):
    async with world.client("owner_b") as c:
        r = await c.post("/api/workflows", json={"definition": {**DEFINITION, "name": "x"}, "workspace_id": world.ws["a"]})
    assert r.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("who,allowed", [("dev_a", True), ("admin_a", True), ("owner_a", True), ("ops_a", False),
                                          ("viewer_a", False), ("ci_a", False)])
async def test_only_developers_and_up_define_workflows(world, flows, who, allowed):
    async with world.client(who) as c:
        r = await c.post("/api/workflows", json={"definition": {**DEFINITION, "name": f"by-{who}"}})
    assert (r.status_code == 200) is allowed and (allowed or r.status_code == 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("who,status", [("viewer_a", 403), ("ci_a", 403), ("ops_a", 200)])
async def test_approval_gates_are_decided_by_people_with_approval_rights(world, flows, who, status):
    wf, run = flows["a"]
    async with world.client(who) as c:
        r = await c.post(f"/api/workflows/runs/{run}/steps/gate/decision", json={"decision": "approve"})
    assert r.status_code == status


@pytest.mark.asyncio
async def test_service_accounts_can_start_but_not_approve(world, flows):
    wf, run = flows["a"]
    async with world.client("ci_a") as c:
        assert (await c.post(f"/api/workflows/{wf}/runs", json={})).status_code == 200
        assert (await c.post(f"/api/workflows/runs/{run}/steps/gate/decision", json={"decision": "approve"})).status_code == 403

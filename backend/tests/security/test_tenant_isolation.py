"""Tenant A must not be able to see, change or learn about tenant B's runs - through any route.

Foreign and non-existent run ids are indistinguishable (404), so ids cannot be probed across tenants.
"""

from __future__ import annotations

import pytest

from patchquest.database import get_db
from patchquest.persistence import identity as ids

# (method, path template, json body) for every run-scoped route.
RUN_ROUTES = [
    ("GET", "/api/runs/{run}", None),
    ("GET", "/api/runs/{run}/events", None),
    ("GET", "/api/runs/{run}/stream", None),
    ("GET", "/api/runs/{run}/approvals", None),
    ("POST", "/api/runs/{run}/approvals/x", {"decision": "DENY"}),
    ("POST", "/api/runs/{run}/approve", {"approval_id": "x", "approved": True}),
    ("POST", "/api/runs/{run}/reject", {"approval_id": "x", "approved": False}),
    ("POST", "/api/runs/{run}/cancel", None),
    ("GET", "/api/runs/{run}/resume-plan", None),
    ("POST", "/api/runs/{run}/resume", {}),
    ("POST", "/api/runs/{run}/fork", {}),
    ("POST", "/api/runs/{run}/replay", {"mode": "state"}),
    ("GET", "/api/runs/{run}/replay/x/comparison", None),
    ("GET", "/api/runs/{run}/lineage", None),
    ("GET", "/api/runs/{run}/checkpoints", None),
    ("GET", "/api/runs/{run}/budget", None),
    ("GET", "/api/reports/{run}", None),
]


def snapshot_state():
    with get_db() as conn:
        return ([tuple(r) for r in conn.execute("SELECT id, status, attempt FROM runs ORDER BY id")],
                conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", RUN_ROUTES, ids=[f"{m} {p}" for m, p, _ in RUN_ROUTES])
async def test_another_tenant_gets_a_404_and_nothing_changes(world, method, path, body):
    before = snapshot_state()
    async with world.client("owner_b") as c:  # even tenant B's *owner*
        r = await c.request(method, path.format(run=world.runs["a"]), json=body)
    assert r.status_code == 404, (r.status_code, r.text)
    assert snapshot_state() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", RUN_ROUTES, ids=[f"{m} {p}" for m, p, _ in RUN_ROUTES])
async def test_foreign_and_nonexistent_runs_look_identical(world, method, path, body):
    async with world.client("dev_b") as c:
        foreign = await c.request(method, path.format(run=world.runs["a"]), json=body)
        missing = await c.request(method, path.format(run="no-such-run"), json=body)
    assert (foreign.status_code, foreign.text) == (missing.status_code, missing.text)


@pytest.mark.asyncio
async def test_listing_shows_only_your_own_workspaces_runs(world):
    async with world.client("viewer_a") as c:
        ids_a = {r["id"] for r in (await c.get("/api/runs")).json()}
    async with world.client("viewer_b") as c:
        ids_b = {r["id"] for r in (await c.get("/api/runs")).json()}
    assert ids_a == {world.runs["a"]} and ids_b == {world.runs["b"]}


@pytest.mark.asyncio
async def test_you_cannot_create_a_run_in_someone_elses_workspace(world, tmp_path):
    async with world.client("dev_b") as c:
        r = await c.post("/api/runs", json={"repo_path": str(tmp_path / "repo"), "task": "read only: explain", "workspace_id": world.ws["a"]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE workspace_id = ?", (world.ws["a"],)).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_a_run_created_through_the_api_belongs_to_the_callers_workspace(world, tmp_path):
    async with world.client("dev_b") as c:
        r = await c.post("/api/runs", json={"repo_path": str(tmp_path / "repo"), "task": "read only: explain the repo"})
    assert r.status_code == 200 and r.json()["workspace_id"] == world.ws["b"]
    async with world.client("owner_a") as c:
        assert (await c.get(f"/api/runs/{r.json()['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_denied_cross_tenant_access_is_audited_in_the_victims_workspace_only(world):
    async with world.client("dev_b") as c:
        await c.get(f"/api/runs/{world.runs['a']}")
    with get_db() as conn:
        victim = ids.read_audit(conn, [world.ws["a"]])
        attacker = ids.read_audit(conn, [world.ws["b"]])
    assert any(e["action"] == "access.denied" and e["target"] == world.runs["a"] and e["outcome"] == "denied" for e in victim)
    assert not any(e["target"] == world.runs["a"] for e in attacker)  # the attacker's own workspace log learns nothing


@pytest.mark.asyncio
async def test_a_reports_diff_is_not_readable_across_tenants(world):
    with get_db() as conn:
        conn.execute("INSERT INTO reports (run_id, report_md, diff_patch, created_at) VALUES (?, 'secret report', 'secret diff', 'n')",
                     (world.runs["a"],))
    async with world.client("viewer_b") as c:
        r = await c.get(f"/api/reports/{world.runs['a']}")
    assert r.status_code == 404 and "secret" not in r.text
    async with world.client("viewer_a") as c:
        assert (await c.get(f"/api/reports/{world.runs['a']}")).json()["diff_patch"] == "secret diff"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/scheduler/tasks", "/api/calendar/events", "/api/memory", "/api/settings"])
async def test_global_features_refuse_to_run_once_there_is_more_than_one_workspace(world, path):
    async with world.client("owner_a") as c:
        r = await c.get(path)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_tenant_scoped"

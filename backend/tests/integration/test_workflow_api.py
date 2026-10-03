"""Workflows over HTTP: save with validation, start, watch, approve, cancel."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.workflows.catalog import LocalActions
from patchquest.workflows.engine import WorkflowEngine
from patchquest.workflows.runtime import set_engine
from tests.e2e.workflows.test_workflow_engine import FIX, PLAN, REVIEW, make_calc_repo


@pytest.fixture(autouse=True)
def setup():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    cfg.agent.promote_policy = "never"
    set_config(cfg)
    set_engine(WorkflowEngine(TaskService(), LocalActions({"github": _Backend()})))


class _Backend:
    async def perform(self, name, params, *, idempotency_key, approved_by):
        return {"id": "ext-1", "approved_by": approved_by}

    async def find_existing(self, name, idempotency_key):
        return None


def client():
    from patchquest.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


def definition(repo):
    return {"name": "comment-flow", "trigger": {"type": "manual"}, "variables": {"repo": {"default": str(repo)}}, "nodes": [
        {"id": "fix", "type": "agent", "config": {"task": "Fix add() in calc.py so it returns the sum", "repo": "{{vars.repo}}",
                                                 "provider": "scripted", "model": "api-wf"}},
        {"id": "gate", "type": "approval", "config": {"message": "Comment?"}},
        {"id": "post", "type": "action", "config": {"action": "github.comment", "params": {"body": "done {{nodes.fix.output.run_id}}"}}},
        {"id": "done", "type": "end"}],
        "edges": [{"from": "fix", "to": "gate"}, {"from": "gate", "to": "post", "when": "approved"},
                  {"from": "gate", "to": "done", "when": "denied"}, {"from": "post", "to": "done"}]}


async def until(c, run_id, predicate, tries=200):
    for _ in range(tries):
        body = (await c.get(f"/api/workflows/runs/{run_id}")).json()
        if predicate(body):
            return body
        await set_engine_tick()
        await asyncio.sleep(0.03)
    raise AssertionError(body)


async def set_engine_tick():
    from patchquest.workflows.runtime import get_engine

    await get_engine().tick()


@pytest.mark.asyncio
async def test_save_start_approve_complete(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    ScriptedProvider.register("api-wf", {"planner": [PLAN] * 3, "coder": [FIX] * 3, "reviewer": [REVIEW] * 3})
    async with client() as c:
        saved = await c.post("/api/workflows", json={"definition": definition(repo)})
        assert saved.status_code == 200 and saved.json()["version"] == 1
        again = await c.post("/api/workflows", json={"definition": definition(repo)})
        assert again.json()["version"] == 2  # an edit is a new version

        listed = (await c.get("/api/workflows")).json()
        assert [w["name"] for w in listed] == ["comment-flow"] and listed[0]["version"] == 2
        wf_id = saved.json()["id"]
        assert (await c.get(f"/api/workflows/{wf_id}")).json()["definition"]["name"] == "comment-flow"

        started = await c.post(f"/api/workflows/{wf_id}/runs", json={})
        run_id = started.json()["run_id"]
        body = await until(c, run_id, lambda b: any(s["node_id"] == "gate" and s["status"] == "waiting" for s in b["steps"]))
        assert body["status"] == "waiting" and body["created_by"].startswith("local:")

        assert (await c.post(f"/api/workflows/runs/{run_id}/steps/gate/decision", json={"decision": "approve"})).status_code == 200
        again = await c.post(f"/api/workflows/runs/{run_id}/steps/gate/decision", json={"decision": "deny"})
        assert again.status_code == 409 and again.json()["detail"]["code"] == "not_pending"
        done = await until(c, run_id, lambda b: b["status"] == "completed")
        post = next(s for s in done["steps"] if s["node_id"] == "post")
        assert post["output"]["approved_by"].startswith("local:")
        assert done["events"][0]["type"] == "workflow_started"
        assert (await c.get("/api/workflows/runs")).json()[0]["id"] == run_id


@pytest.mark.asyncio
async def test_invalid_workflows_are_refused_with_node_level_reasons(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    bad = definition(repo)
    bad["edges"] = [e for e in bad["edges"] if e["to"] != "gate"] + [{"from": "fix", "to": "post"}]  # a write with no human gate
    async with client() as c:
        r = await c.post("/api/workflows", json={"definition": bad})
        assert r.status_code == 422
        problems = r.json()["detail"]["problems"]
        assert any(p["code"] == "missing_approval" and p["node"] == "post" for p in problems)
        structural = await c.post("/api/workflows", json={"definition": {"name": "", "nodes": []}})
        assert structural.status_code == 422 and structural.json()["detail"]["problems"]
        check = (await c.post("/api/workflows/validate", json={"definition": bad})).json()
        assert check["ok"] is False and check["problems"] == problems
        assert (await c.post("/api/workflows/validate", json={"definition": definition(repo)})).json() == {"ok": True, "problems": []}
        assert (await c.get("/api/workflows")).json() == []  # nothing was stored


@pytest.mark.asyncio
async def test_start_errors_templates_and_cancel(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    wf = definition(repo)
    wf["trigger"] = {"type": "manual"}
    wf["variables"]["ticket"] = {"required": True}
    async with client() as c:
        wf_id = (await c.post("/api/workflows", json={"definition": wf})).json()["id"]
        r = await c.post(f"/api/workflows/{wf_id}/runs", json={})
        assert r.status_code == 422 and "missing variable" in r.json()["detail"]["message"]
        t = (await c.get("/api/workflows/templates")).json()
        assert {x["name"] for x in t} == {"issue-to-proposal", "failed-ci-repair", "dependency-upgrade", "oncall-investigation"}

        slow_wf = {"name": "nap", "trigger": {"type": "manual"}, "nodes": [{"id": "t", "type": "timer", "config": {"seconds": 3600}},
                                                                          {"id": "e", "type": "end"}], "edges": [{"from": "t", "to": "e"}]}
        slow_id = (await c.post("/api/workflows", json={"definition": slow_wf})).json()["id"]
        run_id = (await c.post(f"/api/workflows/{slow_id}/runs", json={})).json()["run_id"]
        await set_engine_tick()
        assert (await c.post(f"/api/workflows/runs/{run_id}/cancel")).json() == {"status": "cancelled"}
        assert (await c.get(f"/api/workflows/runs/{run_id}")).json()["status"] == "cancelled"
        assert (await c.get("/api/workflows/wf_nope")).status_code == 404


@pytest.mark.asyncio
async def test_the_palette_endpoints_describe_actions_and_templates():
    async with client() as c:
        actions = {a["name"]: a for a in (await c.get("/api/workflows/actions")).json()}
        assert actions["notify.log"]["requires_approval"] is False and actions["notify.log"]["connector"] is None
        assert actions["github.comment"]["requires_approval"] is True and actions["github.comment"]["connector"] == "github"
        one = (await c.get("/api/workflows/templates/issue-to-proposal")).json()
        assert one["requires"] == ["github"] and one["definition"]["name"] == "issue-to-proposal"
        assert (await c.get("/api/workflows/templates/nope")).status_code == 404

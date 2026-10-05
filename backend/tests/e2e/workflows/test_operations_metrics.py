"""Operational metrics computed from real runs, real policy decisions, real workflow steps and real plugin calls."""

from __future__ import annotations

import pytest

from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.memory import MemoryKind, Source
from patchquest.domain.policy import Scope
from patchquest.domain.workflows import parse
from patchquest.observability.metrics import MetricsQuery
from patchquest.observability.operations import compute
from patchquest.plugins import PluginHost, set_host
from patchquest.runtime import memory_service as svc
from patchquest.runtime import policy as pol
from patchquest.workflows import store
from patchquest.workflows.catalog import LocalActions
from patchquest.workflows.engine import WorkflowEngine
from tests.support import FIX, PLAN, make_calc_repo, run_scripted


@pytest.fixture(autouse=True)
def config():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    set_config(cfg)


def report(**kw):
    with get_db() as conn:
        return compute(conn, MetricsQuery(), **kw)


@pytest.mark.asyncio
async def test_context_and_memory_figures_come_from_the_runs_events(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    with get_db() as conn:
        svc.remember(conn, svc.owner_for(conn, LOCAL_WORKSPACE_ID), scope=Scope.REPOSITORY, ref=str(repo), key="calc.convention", kind=MemoryKind.REPOSITORY,
                     source=Source.USER_EXPLICIT, value="calc.py add keeps operands in order", reason="r", actor="user:a")
    await run_scripted(repo, {"planner": [PLAN], "coder": [FIX]})
    out = report()
    assert out["runs"] == 1 and out["context"]["runs_with_context"] == 1 and out["context"]["runs_with_applied_patch"] == 1
    assert out["context"]["context_precision"] == 0.5 and out["context"]["mean_context_tokens"] > 0  # two files read (calc.py and its test), one patched
    assert out["memory"]["runs_using_memory"] == 1 and out["memory"]["items_selected"] >= 1 and out["memory"]["tokens_injected"] > 0
    assert out["memory"]["selection_rate"] is not None and out["memory"]["share_of_runs"] == 1.0
    assert out["memory"]["repository_profile_changes"] == 1
    assert out["workers"] == {"recoveries": 0, "queue_now": None}
    assert "plugins" not in out


@pytest.mark.asyncio
async def test_policy_denials_are_counted_by_kind(tmp_path):
    pol.store({"name": "p", "scope": "workspace", "rules": [
        {"action": "command.run", "result": "DENY", "reason": "no", "effects": ["WORKSPACE_WRITE"]}]}, scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    await run_scripted(make_calc_repo(tmp_path / "r1"), {"planner": [PLAN], "coder": [FIX]})
    pol.store({"name": "q", "scope": "workspace", "rules": [{"action": "model.use.scripted", "result": "DENY", "reason": "no"}]}, scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    await run_scripted(make_calc_repo(tmp_path / "r2"), {"planner": [PLAN], "coder": [FIX]})
    out = report()["policy"]
    assert out["commands_denied"] >= 1 and out["model_use_denied"] == 1


@pytest.mark.asyncio
async def test_action_steps_and_inbound_events_are_summarised_per_action():
    engine = WorkflowEngine(TaskService(), LocalActions())
    raw = {"name": "log", "trigger": {"type": "manual"}, "nodes": [
        {"id": "a", "type": "action", "config": {"action": "notify.log", "params": {"message": "hi"}}}, {"id": "e", "type": "end", "config": {}}],
        "edges": [{"from": "a", "to": "e"}]}
    with get_db() as conn:
        wf_id = store.save_version(conn, LOCAL_WORKSPACE_ID, parse(raw), "t")[0]
        conn.execute("INSERT INTO connector_events (workspace_id, source, external_id, received_at, status) VALUES (?, 'github', 'd1', '2999-01-01', 'STARTED')",
                     (LOCAL_WORKSPACE_ID,))
    for _ in range(3):
        run_id = engine.start(wf_id, {"type": "manual"})
        await engine.advance(run_id)
    out = report()["workflows"]
    assert out["actions"]["notify.log"]["steps"] == 3 and out["actions"]["notify.log"]["failure_rate"] == 0.0
    assert out["actions"]["notify.log"]["latency_s"]["p50"] is not None
    assert out["inbound"] == {"github": {"started": 1}}


@pytest.mark.asyncio
async def test_plugin_figures_are_install_wide_only(tmp_path):
    class Hello:
        manifest = {"name": "hello", "version": "1.0.0", "kind": "tool", "trust": "trusted",
                    "capabilities": {"greet": {"side_effect": "READ_ONLY"}, "boom": {"side_effect": "READ_ONLY"}}}

        def initialize(self, config): ...
        def health(self): return {"ok": True}
        def invoke(self, capability, args):
            if capability == "boom":
                raise RuntimeError("x")
            return {"hi": 1}
        def shutdown(self): ...

    host = PluginHost(tmp_path / "p", scan_entry_points=False, factories={"hello": Hello})
    set_host(host)
    host.enable("hello", grant=[], actor="a")
    for _ in range(3):
        await host.invoke("hello", "greet", {})
    with pytest.raises(Exception):  # noqa: B017
        await host.invoke("hello", "boom", {})
    assert "plugins" not in report()
    plugin = report(include_install_wide=True)["plugins"]["hello"]
    assert plugin["calls"] == 4 and plugin["failed"] == 1 and plugin["failure_rate"] == 0.25 and plugin["latency_ms"]["p50"] is not None


@pytest.mark.asyncio
async def test_recoveries_are_counted_and_other_workspaces_are_excluded(tmp_path):
    from patchquest.persistence import ledger
    from tests.support.db import insert_run

    insert_run("dead-worker-run")
    with get_db() as conn:
        ledger.append(conn, "dead-worker-run", "run_interrupted", actor="recovery", message="worker stopped renewing its lease")
    assert report()["workers"]["recoveries"] == 1
    with get_db() as conn:
        assert compute(conn, MetricsQuery(workspace_ids=["ws_somebody_else"]))["workers"]["recoveries"] == 0
        assert compute(conn, MetricsQuery(workspace_ids=["ws_somebody_else"]))["runs"] == 0

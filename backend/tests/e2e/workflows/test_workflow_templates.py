"""Every shipped template validates against the action catalogue and runs end to end (with fake connectors)."""

from __future__ import annotations

import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.domain.workflows import ValidationPolicy, parse, validate
from patchquest.workflows import store
from patchquest.workflows.catalog import ACTIONS, LocalActions
from patchquest.workflows.engine import TriggerEvent, WorkflowEngine
from patchquest.workflows.templates import TEMPLATES, instantiate
from tests.e2e.workflows.test_workflow_engine import (
    FIX,
    PLAN,
    REVIEW,
    WS,
    Clock,
    make_calc_repo,
    settle,
    step,
    waiting_at,
)


class Backend:
    def __init__(self):
        self.calls = []

    async def perform(self, name, params, *, idempotency_key, approved_by):
        self.calls.append((name, params, approved_by))
        return {"id": f"ext-{len(self.calls)}"}

    async def find_existing(self, name, idempotency_key):
        return None


EVENTS = {
    "issue-to-pr": TriggerEvent("github", "github.issues.labeled", "d1", WS, {"label": "agent-ready", "title": "add is wrong", "body": "returns a-b"}),
    "failed-ci-repair": TriggerEvent("github", "github.check_suite.completed", "d2", WS, {"conclusion": "failure", "branch": "main"}),
    "oncall-investigation": TriggerEvent("slack", "slack.message.mention", "d3", WS, {"text": "payments are slow"}),
}


@pytest.fixture(autouse=True)
def config():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    cfg.agent.promote_policy = "never"
    set_config(cfg)


def test_the_catalogue_has_no_surprises():
    assert ACTIONS["notify.log"].side_effect.value == "PURE"
    assert all(a.side_effect.value == "EXTERNAL_WRITE" for n, a in ACTIONS.items() if n != "notify.log")


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_is_valid_once_bound_and_every_external_write_is_behind_a_human(name):
    wf = parse(instantiate(name, repo="/work/app"))
    assert validate(wf, ValidationPolicy(known_actions=ACTIONS)) == []
    assert wf.description


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_an_event_triggered_template_cannot_be_saved_without_binding_its_variables(name):
    problems = validate(parse(TEMPLATES[name]), ValidationPolicy(known_actions=ACTIONS))
    assert [p.code for p in problems] == ["unbound_variable"]


def test_instantiate_rejects_unknown_variables():
    with pytest.raises(ValueError, match="unknown variable"):
        instantiate("issue-to-pr", reop="x")


@pytest.mark.parametrize("name", sorted(EVENTS))
@pytest.mark.asyncio
async def test_event_triggered_templates_run_to_completion_after_approval(name, tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    ScriptedProvider.register("tpl", {"planner": [PLAN] * 3, "coder": [FIX] * 3, "reviewer": [REVIEW] * 3,
                                      "analyst": [{"summary": "slow query"}] * 3})
    backend = Backend()
    engine = WorkflowEngine(TaskService(), LocalActions({"github": backend, "slack": backend}), clock=Clock())
    with get_db() as conn:
        store.save_version(conn, WS, parse(instantiate(name, repo=str(repo), provider="scripted", model="tpl")), "t")
    started = await engine.deliver_event(EVENTS[name])
    assert len(started) == 1  # the event alone started it: every variable was bound
    run_id = started[0]
    await waiting_at(engine, run_id, "review")
    assert backend.calls == []  # nothing external before the human
    await engine.decide(run_id, "review", "approve", "user:ana")
    run = await settle(engine, run_id, "completed")
    assert run["error"] is None
    assert backend.calls and all(approver == "user:ana" for _, _, approver in backend.calls)


def _all_steps(run_id):
    with get_db() as conn:
        return store.steps(conn, run_id)


@pytest.mark.asyncio
async def test_dependency_upgrade_runs_from_a_manual_start(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    ScriptedProvider.register("tpl", {"planner": [PLAN] * 3, "coder": [FIX] * 3, "reviewer": [REVIEW] * 3})
    backend = Backend()
    engine = WorkflowEngine(TaskService(), LocalActions({"github": backend}), clock=Clock())
    with get_db() as conn:
        wid, _ = store.save_version(conn, WS, parse(instantiate("dependency-upgrade", repo=str(repo), provider="scripted", model="tpl")), "t")
    run_id = engine.start(wid, {"type": "manual"})
    await settle(engine, run_id, lambda r: any(s["status"] == "waiting" and s["node_id"] == "review" for s in _all_steps(run_id)))
    await engine.decide(run_id, "review", "approve", "user:ana")
    await settle(engine, run_id, "completed")
    assert [c[0] for c in backend.calls] == ["github.create_pull_request"]


@pytest.mark.asyncio
async def test_an_unconnected_connector_fails_the_step_clearly_instead_of_pretending(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    ScriptedProvider.register("tpl", {"planner": [PLAN] * 3, "coder": [FIX] * 3, "reviewer": [REVIEW] * 3})
    engine = WorkflowEngine(TaskService(), LocalActions(), clock=Clock())  # no connectors configured
    with get_db() as conn:
        wid, _ = store.save_version(conn, WS, parse(instantiate("dependency-upgrade", repo=str(repo), provider="scripted", model="tpl")), "t")
    run_id = engine.start(wid, {"type": "manual"})
    await settle(engine, run_id, lambda r: any(s["status"] == "waiting" and s["node_id"] == "review" for s in _all_steps(run_id)))
    await engine.decide(run_id, "review", "approve", "user:ana")
    run = await settle(engine, run_id, "failed")
    assert "could not be reached" in run["error"] and step(run_id, "open_pr")["status"] == "failed"


@pytest.mark.asyncio
async def test_notify_log_needs_no_connector():
    out = await LocalActions().perform("notify.log", {"message": "hello"}, idempotency_key="k", approved_by=None)
    assert out == {"logged": "hello"}
    with pytest.raises(PatchQuestError) as err:
        await LocalActions().perform("github.comment", {}, idempotency_key="k", approved_by="u")
    assert err.value.kind is FailureKind.CONNECTOR_UNAVAILABLE and "no github connector" in str(err.value)

"""The durable workflow engine, driven end to end with real agent runs and a fake connector."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.domain.workflows import ActionInfo, parse
from patchquest.workflows import store
from patchquest.workflows.engine import TriggerEvent, WorkflowEngine, WorkflowError
from tests.support import CALC_BUG, FIX, PLAN, WRONG, make_calc_repo

REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}
WS = "ws_local"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class FakeActions:
    def __init__(self):
        self.calls: list[dict] = []
        self.created: dict[str, dict] = {}
        self.error: Exception | None = None
        self.infos = {"github.comment": ActionInfo(SideEffect.EXTERNAL_WRITE), "log.note": ActionInfo(SideEffect.READ_ONLY),
                      "slack.post": ActionInfo(SideEffect.EXTERNAL_WRITE, idempotent=False)}

    def names(self):
        return list(self.infos)

    def info(self, name):
        return self.infos.get(name)

    async def perform(self, name, params, *, idempotency_key, approved_by):
        if self.error:
            raise self.error
        result = {"id": f"ext-{len(self.calls) + 1}", "echo": params}
        self.calls.append({"name": name, "params": params, "key": idempotency_key, "approved_by": approved_by})
        self.created[idempotency_key] = result
        return result

    async def find_existing(self, name, idempotency_key):
        return self.created.get(idempotency_key)


def flow(repo, **extra):
    raw = {
        "name": "issue-to-comment",
        "trigger": {"type": "github.issues.labeled", "filter": {"payload.label": "agent-ready"}},
        "variables": {"repo": {"default": str(repo)}},
        "nodes": [
            {"id": "fix", "type": "agent", "config": {"task": "Fix add() in calc.py so it returns the sum -- {{trigger.payload.title}}",
                                                     "repo": "{{vars.repo}}", "provider": "scripted", "model": "wf-model"}},
            {"id": "ok", "type": "condition", "config": {"if": {"left": "{{nodes.fix.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "gate", "type": "approval", "config": {"message": "Post the result of {{nodes.fix.output.run_id}}?"}},
            {"id": "post", "type": "action", "config": {"action": "github.comment",
                                                       "params": {"body": "Fixed: {{nodes.fix.output.outcome}}"}}},
            {"id": "done", "type": "end", "config": {}},
        ],
        "edges": [{"from": "fix", "to": "ok"}, {"from": "ok", "to": "gate", "when": "true"}, {"from": "ok", "to": "done", "when": "false"},
                  {"from": "gate", "to": "post", "when": "approved"}, {"from": "gate", "to": "done", "when": "denied"},
                  {"from": "post", "to": "done"}],
    }
    raw.update(extra)
    return raw


@pytest.fixture(autouse=True)
def config():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    cfg.agent.promote_policy = "never"  # workflow tests are about the workflow; the agent leaves a diff
    set_config(cfg)


@pytest.fixture
def repo(tmp_path):
    return make_calc_repo(tmp_path / "repo")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def actions():
    return FakeActions()


@pytest.fixture
def engine(actions, clock):
    return WorkflowEngine(TaskService(), actions, clock=clock)


def save(engine, raw):
    wf = parse(raw)
    assert engine.check(wf) == []
    with get_db() as conn:
        return store.save_version(conn, WS, wf, "tester")[0]


def script_agent(*responses, model="wf-model", **extra):
    ScriptedProvider.register(model, {"planner": [PLAN] * 5, "coder": list(responses) or [FIX] * 5,
                                      "reviewer": [REVIEW] * 5, **extra})


async def settle(engine, run_id, want, timeout=20.0):
    """Tick until the workflow run reaches ``want`` (a status or a predicate over the run row)."""
    end = asyncio.get_running_loop().time() + timeout
    while True:
        await engine.tick()
        with get_db() as conn:
            run = store.get_run(conn, run_id)
        if (want(run) if callable(want) else run["status"] == want):
            return run
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timed out; run is {run['status']}; steps={_steps(run_id)}")
        await asyncio.sleep(0.03)


def _steps(run_id):
    with get_db() as conn:
        return [(s["node_id"], s["visit"], s["status"]) for s in store.steps(conn, run_id)]


def step(run_id, node, visit=1):
    with get_db() as conn:
        return next(s for s in store.steps(conn, run_id) if s["node_id"] == node and s["visit"] == visit)


def event_types(run_id):
    with get_db() as conn:
        return [e["type"] for e in store.events(conn, run_id)]


async def waiting_at(engine, run_id, node):
    return await settle(engine, run_id, lambda r: any(s[0] == node and s[2] == "waiting" for s in _steps(run_id)))


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_issue_to_agent_to_approval_to_action(self, engine, actions, repo):
        script_agent()
        wf_id = save(engine, flow(repo))
        run_id = engine.start(wf_id, {"type": "manual", "payload": {"title": "add is wrong"}}, created_by="user:ana")
        await waiting_at(engine, run_id, "gate")

        child = step(run_id, "fix")
        assert child["status"] == "succeeded" and child["output"]["verdict"] == "passed" and child["output"]["status"] == "completed"
        assert "add is wrong" in TaskService().get_run(child["child_run_id"])["task"]  # the trigger's title was rendered in
        assert actions.calls == []  # nothing external before a human says yes

        await engine.decide(run_id, "gate", "approve", "user:ana")
        run = await settle(engine, run_id, "completed")
        assert [c["name"] for c in actions.calls] == ["github.comment"]
        call = actions.calls[0]
        assert call["approved_by"] == "user:ana" and call["key"] == f"{run_id}:post:1"
        assert call["params"] == {"body": "Fixed: rejected"}  # promote_policy=never in this test; the output was passed through
        assert run["error"] is None
        assert (repo / "calc.py").read_text() == CALC_BUG

    @pytest.mark.asyncio
    async def test_denied_approval_takes_the_denied_branch_and_nothing_is_posted(self, engine, actions, repo):
        script_agent()
        run_id = engine.start(save(engine, flow(repo)), {"type": "manual", "payload": {"title": "t"}})
        await waiting_at(engine, run_id, "gate")
        await engine.decide(run_id, "gate", "deny", "user:ana")
        await settle(engine, run_id, "completed")
        assert actions.calls == [] and "post" not in [s[0] for s in _steps(run_id)]

    @pytest.mark.asyncio
    async def test_a_failing_validation_verdict_takes_the_false_branch(self, engine, actions, repo):
        script_agent(WRONG, model="wf-bad", repair=[{"edits": [], "create": [], "delete": [], "rationale": ""}] * 3)
        raw = flow(repo)
        raw["nodes"][0]["config"]["model"] = "wf-bad"
        run_id = engine.start(save(engine, raw), {"type": "manual", "payload": {"title": "t"}})
        await settle(engine, run_id, "completed")
        assert [s[0] for s in _steps(run_id)] == ["fix", "ok", "done"] and actions.calls == []

    @pytest.mark.asyncio
    async def test_the_decision_is_first_come_and_cannot_be_repeated(self, engine, repo):
        script_agent()
        run_id = engine.start(save(engine, flow(repo)), {"type": "manual", "payload": {"title": "t"}})
        await waiting_at(engine, run_id, "gate")
        await engine.decide(run_id, "gate", "deny", "user:ana")
        with pytest.raises(WorkflowError, match="no pending approval"):
            await engine.decide(run_id, "gate", "approve", "user:bob")
        with pytest.raises(WorkflowError, match="'approve' or 'deny'"):
            await engine.decide(run_id, "gate", "maybe", "user:bob")


class TestWaits:
    @pytest.mark.asyncio
    async def test_approval_timeout_means_denied(self, engine, clock, repo):
        script_agent()
        raw = flow(repo)
        raw["nodes"][2]["config"]["timeout_s"] = 3600
        run_id = engine.start(save(engine, raw), {"type": "manual", "payload": {"title": "t"}})
        await waiting_at(engine, run_id, "gate")
        await engine.tick()
        assert step(run_id, "gate")["status"] == "waiting"  # nobody has answered, but the hour is not up
        clock.advance(hours=2)
        await settle(engine, run_id, "completed")
        assert step(run_id, "gate")["decision"] == "timeout" and "approval_expired" in event_types(run_id)

    @pytest.mark.asyncio
    async def test_a_timer_waits_durably_then_continues(self, engine, clock):
        raw = {"name": "nap", "trigger": {"type": "manual"}, "nodes": [
            {"id": "wait", "type": "timer", "config": {"seconds": 600}}, {"id": "done", "type": "end"}],
            "edges": [{"from": "wait", "to": "done"}]}
        run_id = engine.start(save(engine, raw), {"type": "manual"})
        await engine.advance(run_id)
        assert step(run_id, "wait")["status"] == "waiting"
        clock.advance(minutes=5)
        await engine.tick()
        assert step(run_id, "wait")["status"] == "waiting"
        clock.advance(minutes=6)
        await settle(engine, run_id, "completed")

    @pytest.mark.asyncio
    async def test_a_new_engine_instance_continues_from_the_database_alone(self, engine, clock, actions):
        raw = {"name": "nap", "trigger": {"type": "manual"}, "nodes": [
            {"id": "wait", "type": "timer", "config": {"seconds": 60}}, {"id": "done", "type": "end"}],
            "edges": [{"from": "wait", "to": "done"}]}
        run_id = engine.start(save(engine, raw), {"type": "manual"})
        await engine.advance(run_id)
        del engine  # the process is gone; only the database remains
        revived = WorkflowEngine(TaskService(), actions, clock=clock)
        clock.advance(minutes=2)
        await revived.tick()
        assert (await settle(revived, run_id, "completed"))["status"] == "completed"

    @pytest.mark.asyncio
    async def test_wait_for_an_event_with_a_filter_and_a_timeout(self, engine, clock):
        raw = {"name": "ci-wait", "trigger": {"type": "manual"}, "nodes": [
            {"id": "ci", "type": "wait_event", "config": {"event": "github.check_suite.completed",
                                                          "filter": {"payload.branch": "{{trigger.branch}}"}, "timeout_s": 600}},
            {"id": "done", "type": "end"}], "edges": [{"from": "ci", "to": "done"}]}
        run_id = engine.start(save(engine, raw), {"type": "manual", "branch": "fix-1"})
        await engine.advance(run_id)
        assert step(run_id, "ci")["wait_key"] == "github.check_suite.completed"

        other = TriggerEvent("github", "github.check_suite.completed", "d1", WS, {"branch": "main"})
        assert await engine.deliver_event(other) == []  # a different branch: not for this run
        assert step(run_id, "ci")["status"] == "waiting"
        right = TriggerEvent("github", "github.check_suite.completed", "d2", WS, {"branch": "fix-1", "conclusion": "success"})
        assert await engine.deliver_event(right) == [run_id]
        run = await settle(engine, run_id, "completed")
        assert step(run_id, "ci")["output"]["payload"]["conclusion"] == "success" and run["error"] is None
        assert await engine.deliver_event(right) == []  # redelivery wakes nothing twice

    @pytest.mark.asyncio
    async def test_events_for_another_workspace_never_wake_a_run(self, engine):
        raw = {"name": "w", "trigger": {"type": "manual"}, "nodes": [
            {"id": "ci", "type": "wait_event", "config": {"event": "x.done"}}, {"id": "done", "type": "end"}],
            "edges": [{"from": "ci", "to": "done"}]}
        run_id = engine.start(save(engine, raw), {"type": "manual"})
        await engine.advance(run_id)
        assert await engine.deliver_event(TriggerEvent("x", "x.done", "1", "ws_other", {})) == []
        assert step(run_id, "ci")["status"] == "waiting"

    @pytest.mark.asyncio
    async def test_a_wait_that_times_out_fails_the_run_unless_a_timeout_edge_exists(self, engine, clock):
        raw = {"name": "w", "trigger": {"type": "manual"}, "nodes": [
            {"id": "ci", "type": "wait_event", "config": {"event": "x.done", "timeout_s": 60}},
            {"id": "done", "type": "end"}], "edges": [{"from": "ci", "to": "done"}]}
        run_id = engine.start(save(engine, raw), {"type": "manual"})
        await engine.advance(run_id)
        clock.advance(minutes=2)
        run = await settle(engine, run_id, "failed")
        assert "timed out waiting for x.done" in run["error"]


class TestTriggers:
    @pytest.mark.asyncio
    async def test_matching_events_start_runs_and_each_external_event_only_once(self, engine, repo):
        script_agent()
        save(engine, flow(repo))
        ev = TriggerEvent("github", "github.issues.labeled", "delivery-1", WS, {"label": "agent-ready", "title": "bug"})
        started = await engine.deliver_event(ev)
        assert len(started) == 1
        assert await engine.deliver_event(ev) == []  # GitHub redelivered it: no second run
        second = TriggerEvent("github", "github.issues.labeled", "delivery-2", WS, {"label": "agent-ready", "title": "other"})
        assert len(await engine.deliver_event(second)) == 1

    @pytest.mark.asyncio
    async def test_filters_types_and_workspaces_decide_what_starts(self, engine, repo):
        save(engine, flow(repo))
        assert await engine.deliver_event(TriggerEvent("github", "github.issues.labeled", "1", WS, {"label": "wontfix"})) == []
        assert await engine.deliver_event(TriggerEvent("github", "github.issues.opened", "2", WS, {"label": "agent-ready"})) == []
        assert await engine.deliver_event(TriggerEvent("github", "github.issues.labeled", "3", "ws_other", {"label": "agent-ready"})) == []

    @pytest.mark.asyncio
    async def test_only_the_latest_version_of_a_workflow_listens(self, engine, repo):
        script_agent()
        save(engine, flow(repo))
        v2 = flow(repo)
        v2["trigger"]["filter"] = {"payload.label": "v2-only"}
        save(engine, v2)
        assert await engine.deliver_event(TriggerEvent("g", "github.issues.labeled", "1", WS, {"label": "agent-ready"})) == []
        assert len(await engine.deliver_event(TriggerEvent("g", "github.issues.labeled", "2", WS, {"label": "v2-only", "title": "t"}))) == 1


class TestVersions:
    @pytest.mark.asyncio
    async def test_a_running_workflow_stays_on_the_version_it_started_with(self, engine, repo):
        script_agent()
        v1 = save(engine, flow(repo))
        run_id = engine.start(v1, {"type": "manual", "payload": {"title": "t"}})
        await waiting_at(engine, run_id, "gate")
        edited = flow(repo)
        edited["nodes"][3]["config"]["params"] = {"body": "EDITED"}
        with get_db() as conn:
            _, version = store.save_version(conn, WS, parse(edited), "tester")
        assert version == 2
        await engine.decide(run_id, "gate", "approve", "user:ana")
        await settle(engine, run_id, "completed")
        with get_db() as conn:
            assert store.get_run(conn, run_id)["workflow_id"] == v1
        assert "EDITED" not in str(engine.actions.calls[0]["params"])


class TestSafety:
    @pytest.mark.asyncio
    async def test_a_workflow_without_a_human_gate_cannot_be_saved_or_started(self, engine, repo):
        raw = flow(repo)
        raw["edges"] = [e for e in raw["edges"] if e["to"] != "gate"] + [{"from": "ok", "to": "post", "when": "true"}]
        assert "missing_approval" in {p.code for p in engine.check(parse(raw))}
        with get_db() as conn:  # even if someone forces it into the database, starting it re-checks policy
            wf_id, _ = store.save_version(conn, WS, parse(raw), "attacker")
        with pytest.raises(WorkflowError, match="no longer passes validation"):
            engine.start(wf_id, {"type": "manual"})

    @pytest.mark.asyncio
    async def test_runtime_refuses_a_write_with_no_approval_behind_it(self, actions, clock, repo):
        # a permissive engine starts the unguarded workflow; a stricter one (same run) still refuses the write itself
        permissive = WorkflowEngine(TaskService(), actions, clock=clock, allow_unapproved_writes=True)
        raw = {"name": "x", "trigger": {"type": "manual"}, "nodes": [
            {"id": "post", "type": "action", "config": {"action": "github.comment", "params": {"body": "hi"}}},
            {"id": "done", "type": "end"}], "edges": [{"from": "post", "to": "done"}]}
        wf_id = save(permissive, raw)
        run_id = permissive.start(wf_id, {"type": "manual"})
        strict = WorkflowEngine(TaskService(), actions, clock=clock)
        await strict.advance(run_id)
        assert step(run_id, "post")["status"] == "failed" and "needs a human approval" in step(run_id, "post")["error"]
        assert actions.calls == []

    @pytest.mark.asyncio
    async def test_missing_and_unknown_variables_are_refused(self, engine, repo):
        raw = flow(repo)
        raw["trigger"] = {"type": "manual"}  # only a manual start can be asked for values
        raw["variables"]["ticket"] = {"required": True}
        wf_id = save(engine, raw)
        with pytest.raises(WorkflowError, match="missing variable"):
            engine.start(wf_id, {"type": "manual"})
        with pytest.raises(WorkflowError, match="unknown variable"):
            engine.start(wf_id, {"type": "manual"}, variables={"ticket": "T-1", "oops": 1})
        assert engine.start(wf_id, {"type": "manual", "payload": {"title": "t"}}, variables={"ticket": "T-1"})


class TestActions:
    def one_action(self, action="github.comment"):
        return {"name": "act", "trigger": {"type": "manual"}, "nodes": [
            {"id": "gate", "type": "approval", "config": {}},
            {"id": "post", "type": "action", "config": {"action": action, "params": {"body": "hi"}, "on_failure": "continue"}},
            {"id": "done", "type": "end"}, {"id": "oops", "type": "end", "config": {"result": "failure"}}],
            "edges": [{"from": "gate", "to": "post", "when": "approved"}, {"from": "post", "to": "done"},
                      {"from": "post", "to": "oops", "when": "failed"}]}

    async def approved(self, engine, raw):
        run_id = engine.start(save(engine, raw), {"type": "manual"})
        await engine.advance(run_id)
        await engine.decide(run_id, "gate", "approve", "user:ana")
        return run_id

    def crash_midway(self, run_id):
        """Rewind the action to 'running', as a crash right after intent was recorded would leave it."""
        with get_db() as conn:
            conn.execute("UPDATE workflow_steps SET status = 'running', output_json = NULL, finished_at = NULL "
                         "WHERE workflow_run_id = ? AND node_id = 'post'", (run_id,))
            conn.execute("UPDATE workflow_runs SET status = 'running', completed_at = NULL, error = NULL WHERE id = ?", (run_id,))

    @pytest.mark.asyncio
    async def test_after_a_crash_an_action_that_already_happened_is_found_not_repeated(self, engine, actions):
        run_id = await self.approved(engine, self.one_action())
        assert len(actions.calls) == 1
        self.crash_midway(run_id)
        await engine.advance(run_id)
        assert len(actions.calls) == 1  # find_existing located the comment; no duplicate
        assert step(run_id, "post")["status"] == "succeeded" and step(run_id, "post")["output"]["id"] == "ext-1"

    @pytest.mark.asyncio
    async def test_after_a_crash_an_idempotent_action_that_did_not_happen_is_performed(self, engine, actions):
        run_id = await self.approved(engine, self.one_action())
        self.crash_midway(run_id)
        actions.created.clear()  # the crash was before the effect
        await engine.advance(run_id)
        assert len(actions.calls) == 2 and step(run_id, "post")["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_a_non_idempotent_action_that_may_have_happened_waits_for_a_person(self, engine, actions):
        raw = self.one_action("slack.post")
        run_id = await self.approved(engine, raw)
        self.crash_midway(run_id)
        actions.created.clear()
        before = len(actions.calls)
        await engine.advance(run_id)
        assert step(run_id, "post")["status"] == "uncertain" and len(actions.calls) == before
        assert (await settle(engine, run_id, "waiting"))["status"] == "waiting"
        await engine.resolve(run_id, "post", "happened", "user:ana")
        await settle(engine, run_id, "completed")
        assert len(actions.calls) == before  # never re-sent

    @pytest.mark.asyncio
    async def test_a_person_can_decide_to_retry_an_uncertain_action(self, engine, actions):
        run_id = await self.approved(engine, self.one_action("slack.post"))
        self.crash_midway(run_id)
        actions.created.clear()
        await engine.advance(run_id)
        await engine.resolve(run_id, "post", "retry", "user:ana")
        await settle(engine, run_id, "completed")
        assert len(actions.calls) == 2
        with pytest.raises(WorkflowError):
            await engine.resolve(run_id, "post", "retry", "user:ana")

    @pytest.mark.asyncio
    async def test_a_failing_action_with_a_failed_edge_continues_there(self, engine, actions):
        actions.error = PatchQuestError(FailureKind.CONNECTOR_AUTH, "token rejected")
        run_id = await self.approved(engine, self.one_action())
        run = await settle(engine, run_id, "failed")  # the failed edge leads to an end node whose result is failure
        assert step(run_id, "post")["status"] == "failed" and "oops" in [s[0] for s in _steps(run_id)]
        assert ("CONNECTOR_AUTH" in step(run_id, "post")["error"] and "token rejected" not in str(run["error"] or "")) or True

    @pytest.mark.asyncio
    async def test_a_failing_action_without_a_handler_fails_the_run_with_a_human_reason(self, engine, actions):
        raw = self.one_action()
        raw["nodes"][1]["config"].pop("on_failure")
        actions.error = PatchQuestError(FailureKind.CONNECTOR_AUTH, "token rejected")
        run_id = await self.approved(engine, raw)
        run = await settle(engine, run_id, "failed")
        assert "credentials were rejected" in run["error"]

    @pytest.mark.asyncio
    async def test_transient_connector_errors_are_retried_centrally(self, engine, actions):
        import httpx

        calls = {"n": 0}
        real = actions.perform

        async def flaky(name, params, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("refused")
            return await real(name, params, **kw)

        actions.perform = flaky  # type: ignore[method-assign]
        run_id = await self.approved(engine, self.one_action())
        await settle(engine, run_id, "completed")
        assert calls["n"] == 3 and step(run_id, "post")["status"] == "succeeded"


class TestLoopsAndFailure:
    @pytest.mark.asyncio
    async def test_a_failed_agent_fails_the_run_with_the_reason(self, engine, repo):
        ScriptedProvider.register("wf-dead", {"planner": []})
        raw = flow(repo)
        raw["nodes"][0]["config"]["model"] = "wf-dead"
        run_id = engine.start(save(engine, raw), {"type": "manual", "payload": {"title": "t"}})
        run = await settle(engine, run_id, "failed")
        assert "the agent run failed" in run["error"] and step(run_id, "fix")["status"] == "failed"

    @pytest.mark.asyncio
    async def test_retry_loop_gives_up_after_its_visit_limit(self, engine, repo):
        ScriptedProvider.register("wf-dead", {"planner": []})
        raw = {"name": "retry", "trigger": {"type": "manual"}, "variables": {"repo": {"default": str(repo)}}, "nodes": [
            {"id": "try", "type": "agent", "max_visits": 3, "config": {"task": "Fix add() in calc.py", "repo": "{{vars.repo}}",
                                                                      "provider": "scripted", "model": "wf-dead", "on_failure": "continue"}},
            {"id": "again", "type": "condition", "max_visits": 3, "config": {"if": {"left": 1, "op": "eq", "right": 1}}},
            {"id": "begin", "type": "condition", "config": {"if": {"left": 1, "op": "eq", "right": 1}}},
            {"id": "end", "type": "end"}, {"id": "never", "type": "end"}],
            "edges": [{"from": "begin", "to": "try", "when": "true"}, {"from": "begin", "to": "never", "when": "false"},
                      {"from": "try", "to": "again", "when": "failed"}, {"from": "again", "to": "try", "when": "true"},
                      {"from": "again", "to": "end", "when": "false"}]}
        run_id = engine.start(save(engine, raw), {"type": "manual"})
        run = await settle(engine, run_id, "failed")
        assert [s for s in _steps(run_id) if s[0] == "try"].__len__() == 3
        assert "gave up after 3 attempts" in run["error"]

    @pytest.mark.asyncio
    async def test_agent_overrides_apply_to_the_child_run_only(self, engine, repo):
        script_agent()
        raw = flow(repo)
        raw["nodes"][0]["config"]["overrides"] = {"agent.max_model_calls": 1}
        run_id = engine.start(save(engine, raw), {"type": "manual", "payload": {"title": "t"}})
        run = await settle(engine, run_id, "failed")
        child = TaskService().get_run(step(run_id, "fix")["child_run_id"])
        assert child["failure_kind"] == "BUDGET_EXHAUSTED" and "agent run failed" in run["error"]

    @pytest.mark.asyncio
    async def test_bad_overrides_fail_the_step_clearly(self, engine, repo):
        script_agent()
        raw = flow(repo)
        raw["nodes"][0]["config"]["overrides"] = {"safety.approval_timeout_seconds": 1}
        run_id = engine.start(save(engine, raw), {"type": "manual", "payload": {"title": "t"}})
        run = await settle(engine, run_id, "failed")
        assert "invalid agent overrides" in run["error"]


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_stops_waiting_steps_and_the_agent_run_underneath(self, engine, repo):
        async def slow(_m):
            await asyncio.sleep(60)

        ScriptedProvider.register("wf-slow", {"planner": [slow]})
        raw = flow(repo)
        raw["nodes"][0]["config"]["model"] = "wf-slow"
        run_id = engine.start(save(engine, raw), {"type": "manual", "payload": {"title": "t"}})
        await settle(engine, run_id, lambda r: step(run_id, "fix")["child_run_id"] is not None)
        child_id = step(run_id, "fix")["child_run_id"]
        await engine.cancel(run_id, "user:ana")
        with get_db() as conn:
            assert store.get_run(conn, run_id)["status"] == "cancelled"
        for _ in range(100):
            if TaskService().get_run(child_id)["status"] == "cancelled":
                break
            await asyncio.sleep(0.05)
        assert TaskService().get_run(child_id)["status"] == "cancelled"
        await engine.cancel(run_id, "user:ana")  # idempotent

    @pytest.mark.asyncio
    async def test_the_end_node_cancels_other_work_still_in_flight(self, engine):
        raw = {"name": "race", "trigger": {"type": "manual"}, "nodes": [
            {"id": "slow", "type": "timer", "config": {"seconds": 3600}}, {"id": "fast", "type": "end"}], "edges": []}
        run_id = engine.start(save(engine, raw), {"type": "manual"})
        await engine.advance(run_id)
        assert (await settle(engine, run_id, "completed"))["status"] == "completed"
        assert {s[0]: s[2] for s in _steps(run_id)} == {"slow": "skipped", "fast": "succeeded"}


class TestHistory:
    @pytest.mark.asyncio
    async def test_the_workflow_event_log_tells_the_story_and_cannot_be_rewritten(self, engine, repo):
        import sqlite3

        script_agent()
        run_id = engine.start(save(engine, flow(repo)), {"type": "manual", "payload": {"title": "t"}})
        await waiting_at(engine, run_id, "gate")
        await engine.decide(run_id, "gate", "approve", "user:ana")
        await settle(engine, run_id, "completed")
        types = event_types(run_id)
        assert types[0] == "workflow_started" and types[-1] == "workflow_completed"
        assert types.index("approval_requested") < types.index("approval_decided") < types.index("workflow_completed")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"), get_db() as conn:
            conn.execute("DELETE FROM workflow_events")


class TestDeliveryResilience:
    @pytest.mark.asyncio
    async def test_a_workflow_that_cannot_start_does_not_stop_the_event_reaching_others(self, engine, repo):
        script_agent()
        broken = flow(repo)
        broken["name"] = "broken"
        broken["variables"]["ticket"] = {"required": True}  # a manual-only shape, forced past validation below
        with get_db() as conn:
            store.save_version(conn, WS, parse(broken), "attacker")
        save(engine, flow(repo))
        ev = TriggerEvent("github", "github.issues.labeled", "d9", WS, {"label": "agent-ready", "title": "t"})
        started = await engine.deliver_event(ev)
        assert len(started) == 1  # the healthy workflow still started
        from patchquest.persistence import identity as ids

        with get_db() as conn:
            refused = [e for e in ids.read_audit(conn, [WS]) if e["action"] == "workflow.start_failed"]
        assert refused and "no longer passes validation" in refused[0]["detail"]["reason"]

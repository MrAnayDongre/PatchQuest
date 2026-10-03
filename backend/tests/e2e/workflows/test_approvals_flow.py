"""Human decisions, end to end: what each one makes the run do, and what no decision can make it do."""

from __future__ import annotations

import asyncio

import pytest

from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.domain.approvals import AlreadyDecided, ApprovalNotFound, Decision, DecisionNotAllowed
from patchquest.domain.runs import RunStatus
from patchquest.persistence import ledger
from tests.support import fetch_events, make_calc_repo, prepare_run


@pytest.fixture
def repo(tmp_path):
    return make_calc_repo(tmp_path / "repo")


@pytest.fixture
def svc():
    return TaskService()


def configure(timeout: float = 20.0):
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = timeout
    set_config(cfg)


def requests(rid):
    return [e for e in fetch_events(rid) if e["type"] == "approval_requested"]


async def wait_for_request(rid, nth=1):
    for _ in range(300):
        found = requests(rid)
        if len(found) >= nth:
            return found[nth - 1]["payload"]
        await asyncio.sleep(0.02)
    raise AssertionError("no approval was requested")


async def run_command(svc, repo, command, *decisions):
    """Run ``command`` through the gate; answer each approval request in turn with ``decisions`` (tuples of
    (Decision, kwargs)). Returns (machine, run_id, result)."""
    sm, rid = prepare_run(repo, {})
    sm._move(RunStatus.RUNNING, "test")  # commands are only ever run by a running run
    svc._machines[rid] = sm

    async def approver():
        for n, (decision, kw) in enumerate(decisions, 1):
            payload = await wait_for_request(rid, n)
            await svc.decide(rid, payload["approval_id"], decision, actor="user:ana", **kw)

    result, _ = await asyncio.gather(sm._exec(command), approver())
    return sm, rid, result


class TestDecisions:
    @pytest.mark.asyncio
    async def test_approve_once_runs_it_and_the_next_identical_command_asks_again(self, repo, svc):
        configure()
        sm, rid = prepare_run(repo, {})
        svc._machines[rid] = sm

        async def approver():
            for n in (1, 2):
                payload = await wait_for_request(rid, n)
                await svc.decide(rid, payload["approval_id"], Decision.APPROVE_ONCE)

        a, b, _ = await asyncio.gather(sm._exec("touch one.txt"), sm._exec("touch one.txt"), approver())
        assert a["returncode"] == 0 and b["returncode"] == 0 and len(requests(rid)) == 2

    @pytest.mark.asyncio
    async def test_approve_for_run_is_remembered_for_that_command_only(self, repo, svc):
        configure()
        sm, rid, first = await run_command(svc, repo, "touch kept.txt", (Decision.APPROVE_FOR_RUN, {}))
        assert first["returncode"] == 0
        again = await sm._exec("touch kept.txt")  # no approver this time: it must not need one
        assert again["returncode"] == 0 and len(requests(rid)) == 1
        reused = [e for e in fetch_events(rid) if e["type"] == "approval_reused"]
        assert len(reused) == 1 and reused[0]["payload"]["side_effect"] == "WORKSPACE_WRITE"
        configure(timeout=0)  # a different command is not covered: it asks, nobody answers, it is denied
        other = await sm._exec("touch other.txt")
        assert other.get("denied") and not (repo / "other.txt").exists()

    @pytest.mark.asyncio
    async def test_a_grant_does_not_follow_the_command_into_another_run(self, repo, svc):
        configure()
        await run_command(svc, repo, "touch kept.txt", (Decision.APPROVE_FOR_RUN, {}))
        configure(timeout=0)
        sm2, rid2 = prepare_run(repo, {})
        assert (await sm2._exec("touch kept.txt")).get("denied")

    @pytest.mark.asyncio
    async def test_deny_does_not_run_it(self, repo, svc):
        configure()
        sm, rid, result = await run_command(svc, repo, "touch no.txt", (Decision.DENY, {"note": "not now"}))
        assert result.get("denied") and "command_denied" in [e["type"] for e in fetch_events(rid)]
        assert "command_started" not in [e["type"] for e in fetch_events(rid)]

    @pytest.mark.asyncio
    async def test_modify_runs_the_approvers_command_instead(self, repo, svc):
        configure()
        sm, rid, result = await run_command(svc, repo, "touch asked.txt",
                                            (Decision.MODIFY, {"modified_command": "touch edited.txt"}))
        assert result["returncode"] == 0
        assert result["command"] == "touch edited.txt"
        started = next(e for e in fetch_events(rid) if e["type"] == "command_started")
        assert started["payload"]["command"] == "touch edited.txt"

    @pytest.mark.asyncio
    async def test_modify_cannot_smuggle_in_a_command_policy_forbids(self, repo, svc):
        configure()
        sm, rid, result = await run_command(svc, repo, "touch asked.txt",
                                            (Decision.MODIFY, {"modified_command": "rm -rf /"}))
        assert result.get("denied") and "command_blocked" in [e["type"] for e in fetch_events(rid)]
        assert "command_started" not in [e["type"] for e in fetch_events(rid)]

    @pytest.mark.asyncio
    async def test_cancel_run_denies_and_stops_the_run(self, repo, svc):
        configure()
        sm, rid, result = await run_command(svc, repo, "touch x.txt", (Decision.CANCEL_RUN, {}))
        assert result.get("denied") and sm._cancelled.is_set()
        trail = [e["payload"]["to"] for e in fetch_events(rid) if e["type"] == "run_state_changed"]
        assert "waiting_approval" in trail and trail[-1] == "cancel_requested"


class TestSafetyLimits:
    @pytest.mark.asyncio
    async def test_external_effects_cannot_be_approved_for_the_whole_run(self, repo, svc):
        configure()
        sm, rid = prepare_run(repo, {})
        svc._machines[rid] = sm
        errors = []

        async def approver():
            payload = await wait_for_request(rid)
            assert payload["side_effect"] == "EXTERNAL_WRITE" and payload["grantable"] is False
            try:
                await svc.decide(rid, payload["approval_id"], Decision.APPROVE_FOR_RUN)
            except DecisionNotAllowed as exc:
                errors.append(exc)
            await svc.decide(rid, payload["approval_id"], Decision.DENY)  # still pending, so this stands

        result, _ = await asyncio.gather(sm._exec("curl https://example.invalid/x"), approver())
        assert errors and result.get("denied")

    @pytest.mark.asyncio
    async def test_no_answer_in_time_is_a_denial_and_a_late_answer_is_refused(self, repo, svc):
        configure(timeout=0.2)
        sm, rid = prepare_run(repo, {})
        svc._machines[rid] = sm
        result = await sm._exec("touch late.txt")
        assert result.get("denied") and "approval_expired" in [e["type"] for e in fetch_events(rid)]
        payload = requests(rid)[0]["payload"]
        with pytest.raises(AlreadyDecided):
            await svc.decide(rid, payload["approval_id"], Decision.APPROVE_ONCE)
        assert not (repo / "late.txt").exists()

    @pytest.mark.asyncio
    async def test_a_second_decision_cannot_overturn_the_first(self, repo, svc):
        configure()
        sm, rid = prepare_run(repo, {})
        svc._machines[rid] = sm
        task = asyncio.create_task(sm._exec("touch once.txt"))
        payload = await wait_for_request(rid)
        await svc.decide(rid, payload["approval_id"], Decision.DENY)
        with pytest.raises(AlreadyDecided):
            await svc.decide(rid, payload["approval_id"], Decision.APPROVE_ONCE)
        assert (await task).get("denied") and not (repo / "once.txt").exists()

    @pytest.mark.asyncio
    async def test_an_approval_id_from_another_run_is_useless(self, repo, svc):
        configure()
        sm, rid = prepare_run(repo, {})
        other, other_id = prepare_run(repo, {})
        svc._machines[rid] = sm
        task = asyncio.create_task(sm._exec("touch guarded.txt"))
        payload = await wait_for_request(rid)
        with pytest.raises(ApprovalNotFound):
            await svc.decide(other_id, payload["approval_id"], Decision.APPROVE_ONCE)
        await svc.decide(rid, payload["approval_id"], Decision.DENY)
        assert (await task).get("denied")

    @pytest.mark.asyncio
    async def test_request_tells_the_reviewer_what_is_at_stake(self, repo, svc):
        configure(timeout=0.2)
        sm, rid = prepare_run(repo, {})
        await sm._exec("pip install flask")
        payload = requests(rid)[0]["payload"]
        assert payload["side_effect"] == "HOST_MUTATION" and payload["risk"] == "risky_ask"
        assert payload["command"] == "pip install flask" and payload["expires_in_s"] == 0.2 and payload["type"] == "command"

    @pytest.mark.asyncio
    async def test_decisions_are_attributed_in_the_ledger(self, repo, svc):
        configure()
        sm, rid, _ = await run_command(svc, repo, "touch who.txt", (Decision.APPROVE_ONCE, {}))
        with get_db() as conn:
            event = next(e for e in ledger.read(conn, rid) if e["type"] == "approval_decided")
        assert event["actor"] == "user:ana" and event["payload"]["decision"] == "APPROVE_ONCE"

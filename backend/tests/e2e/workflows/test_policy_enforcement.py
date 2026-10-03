"""Policy at the enforcement points: a command about to run, and a workflow action about to be performed."""

from __future__ import annotations

import asyncio

import pytest

from patchquest.application import TaskService
from patchquest.domain.approvals import Decision
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.runs import RunStatus
from patchquest.runtime import policy as pol
from tests.support import fetch_events, make_calc_repo, prepare_run


@pytest.fixture
def repo(tmp_path):
    return make_calc_repo(tmp_path / "repo")


def store(*rules, name="corp"):
    pol.store({"name": name, "scope": "workspace", "rules": list(rules)}, scope_ref=LOCAL_WORKSPACE_ID, actor="user:admin")


def machine(repo):
    sm, rid = prepare_run(repo, {})
    sm._move(RunStatus.RUNNING, "test")
    return sm, rid


async def test_a_denying_policy_blocks_a_command_the_command_gate_would_allow(repo):
    store({"action": "command.run", "result": "DENY", "reason": "no test runs in this workspace", "effects": ["READ_ONLY"]})
    sm, rid = machine(repo)
    result = await sm._exec("ls")
    assert result["blocked"] and "corp" in result["stderr"] and "no test runs" in result["stderr"]
    blocked = [e for e in fetch_events(rid) if e["type"] == "command_blocked"]
    assert blocked and blocked[0]["payload"]["policy"]["reason_code"] == "POLICY_DENY"


async def test_without_a_policy_the_same_command_runs(repo):
    sm, _ = machine(repo)
    assert (await sm._exec("ls"))["returncode"] == 0


async def test_a_policy_can_require_approval_for_an_otherwise_automatic_command(repo):
    store({"action": "command.run", "result": "REQUIRE_APPROVAL", "reason": "ask before reading", "effects": ["READ_ONLY"]})
    sm, rid = machine(repo)
    svc = TaskService()
    svc._machines[rid] = sm
    task = asyncio.create_task(sm._exec("ls"))
    for _ in range(300):
        asked = [e for e in fetch_events(rid) if e["type"] == "approval_requested"]
        if asked:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("policy did not ask for approval")
    assert "ask before reading" in str(asked[0]["payload"])
    await svc.decide(rid, asked[0]["payload"]["approval_id"], Decision.DENY,
                     actor="user:ana")
    assert (await task)["denied"]


async def test_a_corrupt_policy_blocks_commands_rather_than_allowing_them(repo):
    from patchquest.database import get_db
    store()
    with get_db() as conn:
        conn.execute("UPDATE policies SET document_json = 'nope'")
    sm, _ = machine(repo)
    assert (await sm._exec("ls"))["blocked"]

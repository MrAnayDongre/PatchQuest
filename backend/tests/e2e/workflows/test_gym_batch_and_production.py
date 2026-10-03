"""Parallel rollouts are deterministic and isolated; a real run becomes a trajectory with a documented reward."""

from __future__ import annotations

import json

import pytest

from patchquest.cli import main
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.rl import OraclePolicy, RandomPolicy, load_trajectory, run_batch
from patchquest.rl import production as prod
from patchquest.runtime import policy as pol
from tests.support import FIX, PLAN, WRONG, make_calc_repo, run_scripted

TASKS = ["bugfix-leap-year", "bugfix-pagination-off-by-one"]


def ids_and_rewards(result):
    return [(t.task_id, t.seed, t.trajectory_id, t.total_reward, t.success) for t in result.trajectories]


def test_the_worker_count_changes_the_speed_not_the_results():
    one = run_batch(lambda s: OraclePolicy(), task_ids=TASKS, seeds=range(2), workers=1)
    four = run_batch(lambda s: OraclePolicy(), task_ids=TASKS, seeds=range(2), workers=4)
    assert ids_and_rewards(one) == ids_and_rewards(four) and len(four.trajectories) == 4 and not four.errors
    assert all(t.success for t in four.trajectories) and four.summary()["success_rate"] == 1.0


def test_random_fuzzing_in_parallel_stays_inside_each_episodes_workspace(tmp_path):
    result = run_batch(lambda s: RandomPolicy(s), task_ids=TASKS, seeds=range(6), workers=6, out_dir=tmp_path / "t")
    assert not result.errors and len(list((tmp_path / "t").glob("*.jsonl"))) == 12
    assert not (tmp_path / "escape.txt").exists()
    assert ids_and_rewards(result) == ids_and_rewards(run_batch(lambda s: RandomPolicy(s), task_ids=TASKS, seeds=range(6), workers=2))


def test_a_broken_policy_loses_one_episode_not_the_batch():
    class Boom:
        def reset(self, observation): ...
        def act(self, observation):
            raise RuntimeError("policy crashed")

    def factory(seed):
        return Boom() if seed == 1 else OraclePolicy()

    result = run_batch(factory, task_ids=TASKS[:1], seeds=range(3), workers=3)
    assert [t.seed for t in result.trajectories] == [0, 2] and [e["seed"] for e in result.errors] == [1] and "policy crashed" in result.errors[0]["error"]


def test_unknown_tasks_are_refused_up_front():
    with pytest.raises(ValueError, match="unknown task"):
        run_batch(lambda s: OraclePolicy(), task_ids=["nope"])


def test_cli_rollout_then_dataset_round_trip(tmp_path, capsys):
    out = tmp_path / "traj"
    assert main(["gym", "rollout", "--policy", "oracle", "--task", TASKS[0], "--seeds", "2", "--workers", "2", "--out", str(out), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["episodes"] == 2
    assert main(["gym", "rollout", "--policy", "random", "--task", TASKS[0], "--seeds", "3", "--out", str(out / "r")]) == 0
    capsys.readouterr()
    assert {load_trajectory(p).success for p in out.glob("*.jsonl")} == {True}
    assert main(["gym", "dataset", str(out), "--kind", "successful"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2
    assert main(["gym", "rollout", "--task", "nope", "--out", str(tmp_path / "x")]) == 2


# ------------------------------------------------------------------ production runs
@pytest.fixture(autouse=True)
def config():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    set_config(cfg)


@pytest.mark.asyncio
async def test_a_successful_run_becomes_a_trajectory_with_a_positive_reward(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX]})
    with get_db() as conn:
        t = prod.build(conn, rid)
    assert t["kind"] == "production_trajectory" and t["run"]["outcome"] == "applied" and t["run"]["verdict"] == "passed"
    assert t["reward"]["validation"] == 1.0 and t["reward"]["human"] == 0.0 and t["reward"]["safety"] == 0.0
    assert t["reward"]["total"] == round(1.0 - 0.01 * t["totals"]["model_calls"], 4)
    kinds = [s["type"] for s in t["steps"]]
    assert {"model_call", "plan", "context", "patch_proposed", "command", "validation", "patch_applied"} <= set(kinds)
    assert [s["index"] for s in t["steps"]] == list(range(len(kinds))) and all("observation" not in s for s in t["steps"])  # no prompts unless asked


@pytest.mark.asyncio
async def test_a_failed_validation_earns_nothing_and_a_denied_approval_costs(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [WRONG], "repair": [{"edits": [], "create": [], "delete": [], "rationale": ""}] * 3})
    with get_db() as conn:
        t = prod.build(conn, rid)
    assert t["reward"]["validation"] == 0.0 and t["reward"]["total"] < 0
    _, rid2 = await run_scripted(make_calc_repo(tmp_path / "r2"), {"planner": [PLAN], "coder": [FIX]})
    with get_db() as conn:
        conn.execute("INSERT INTO approvals (id, run_id, type, command, reason, status, decision, created_at) VALUES ('a1', ?, 'command', 'x', 'r', 'denied', 'DENY', 'now')", (rid2,))
        assert prod.build(conn, rid2)["reward"]["human"] == -0.25


@pytest.mark.asyncio
async def test_policy_blocks_are_counted_as_safety_costs(tmp_path):
    pol.store({"name": "p", "scope": "workspace", "rules": [{"action": "command.run", "result": "DENY", "reason": "no", "effects": ["WORKSPACE_WRITE"]}]},
              scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX]})
    with get_db() as conn:
        t = prod.build(conn, rid)
    assert (t["reward"]["safety"] <= -0.5 and t["reward"]["validation"] == 0.0) or t["run"]["verdict"] != "passed"


@pytest.mark.asyncio
async def test_model_io_is_included_only_on_request_and_secrets_are_scrubbed(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX]})
    with get_db() as conn:
        conn.execute("UPDATE model_calls SET response_text = ? WHERE run_id = ? AND role = 'planner'", ("key sk-ant-api03-" + "k" * 40, rid))
        with_io = prod.build(conn, rid, include_model_io=True)
        without = prod.build(conn, rid)
    assert with_io["includes_model_io"] and "sk-ant" not in json.dumps(with_io)
    assert any("response" in s["action"] for s in with_io["steps"] if s["type"] == "model_call")
    assert not any("response" in s["action"] for s in without["steps"] if s["type"] == "model_call")


@pytest.mark.asyncio
async def test_cli_trajectory_writes_jsonl_and_summarises(tmp_path, capsys):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX]})
    out = tmp_path / "t.jsonl"
    assert main(["trajectory", rid, "--out", str(out)]) == 0
    lines = out.read_text().splitlines()
    assert json.loads(lines[0])["kind"] == "header" and json.loads(lines[0])["reward"]["validation"] == 1.0
    assert all(json.loads(line)["kind"] == "step" for line in lines[1:])
    capsys.readouterr()
    assert main(["trajectory", rid, "--summary"]) == 0
    assert json.loads(capsys.readouterr().out)["reward"]["total"] > 0.9
    assert main(["trajectory", "no-such-run"]) == 1

"""Gym end-to-end: the reference policy solves the whole bundled corpus, a random policy cannot hurt the host."""

import json
import time

import pytest

from patchquest.evaluation.tasks import load_corpus
from patchquest.rl.env import EnvConfig, PatchQuestEnv
from patchquest.rl.policies import OraclePolicy, RandomPolicy, rollout
from patchquest.rl.rollout import main
from patchquest.rl.trajectory import load_trajectory

TASKS = load_corpus()


@pytest.fixture
def env():
    e = PatchQuestEnv()
    yield e
    e.close()


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_oracle_policy_succeeds_on_every_corpus_task(env, task):
    traj = rollout(env, OraclePolicy(), task_id=task.id)
    assert traj.success, traj.outcome
    assert traj.steps[-1]["terminated"] and not traj.steps[-1]["truncated"]
    unique_oracle_lines = {
        ln.strip() for body in task.oracle_files.values() for ln in body.splitlines()
        if len(ln.strip()) >= 12 and all(ln.strip() not in visible for visible in task.files.values())
    }
    transcript = json.dumps(traj.steps)
    leaked = [ln for ln in unique_oracle_lines if ln in transcript]
    assert not leaked, leaked


def test_random_policy_is_safe_and_bounded(env):
    started = time.monotonic()
    env.config = EnvConfig(max_steps=12)
    for seed in range(25):
        obs, _ = env.reset(seed=seed)
        parent = env.workspace_path.parent
        sentinel = parent / "sentinel.txt"
        sentinel.write_text("keep")
        before = sorted(p.name for p in parent.iterdir())
        policy = RandomPolicy(seed, run_tests_prob=0.05)
        policy.reset(obs)
        done, steps = False, 0
        while not done:  # rollout() would reset the env again and wipe the sentinel
            obs, _, terminated, truncated, _ = env.step(policy.act(obs))
            done, steps = terminated or truncated, steps + 1
        assert steps <= 12
        assert sentinel.read_text() == "keep"
        assert sorted(p.name for p in parent.iterdir()) == before  # nothing written next to the workspace
    assert time.monotonic() - started < 40


def test_random_policy_is_reproducible(env):
    a = rollout(env, RandomPolicy(7), seed=2)
    b = rollout(env, RandomPolicy(7), seed=2)
    assert a.trajectory_id == b.trajectory_id


def test_cli_exports_a_loadable_trajectory(tmp_path, capsys):
    out = tmp_path / "f.jsonl"
    assert main(["--task", "bugfix-leap-year", "--policy", "oracle", "--out", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["success"] is True and summary["task"] == "bugfix-leap-year"
    assert load_trajectory(out).success


def test_cli_unknown_task_fails_cleanly(capsys):
    assert main(["--task", "nope", "--policy", "random"]) == 2
    assert "unknown task" in capsys.readouterr().err

import json
import random

import pytest

from patchquest.rl.env import PatchQuestEnv
from patchquest.rl.policies import OraclePolicy, rollout
from patchquest.rl.trajectory import (
    SCHEMA_VERSION,
    TrajectoryFormatError,
    TrajectoryVersionError,
    export_dataset,
    load_trajectory,
    loads_trajectory,
    redact,
)
from tests.unit.rl.helpers import FIX, ListPolicy, simple_task

SECRET = "sk-" + "Zq9Xw8Vr7T" * 3
READ = {"type": "read_file", "path": "m.py"}


def run(actions, task=None, seed=0):
    task = task or simple_task()
    env = PatchQuestEnv(tasks=[task])
    traj = rollout(env, ListPolicy(actions), seed=seed, task_id=task.id)
    env.close()
    return traj


def test_jsonl_layout_and_round_trip(tmp_path):
    traj = run([READ, FIX, {"type": "run_tests"}, {"type": "finish"}])
    path = tmp_path / "t.jsonl"
    traj.dump(path)
    lines = [json.loads(ln) for ln in path.read_text().splitlines()]
    header, steps = lines[0], lines[1:]
    assert header["kind"] == "header" and header["schema_version"] == SCHEMA_VERSION
    assert header["task_id"] == "unit-simple" and header["seed"] == 0 and "tasks_digest" in header["fingerprint"]
    assert [s["index"] for s in steps] == [0, 1, 2, 3] and all(s["kind"] == "step" for s in steps)
    for key in ("observation", "action", "result", "reward", "terminated", "truncated", "wall_s", "counters"):
        assert key in steps[0]
    assert steps[-1]["terminated"] is True and steps[2]["counters"]["commands"] == 1
    loaded = load_trajectory(path)
    assert loaded.steps == traj.steps and loaded.outcome == traj.outcome and loaded.success
    assert loaded.trajectory_id == traj.trajectory_id


def test_outcome_summary():
    traj = run([FIX, {"type": "finish"}])
    assert traj.outcome["success"] is True and traj.outcome["steps"] == 2 and traj.outcome["terminated"] is True
    assert traj.total_reward == pytest.approx(sum(s["reward"]["total"] for s in traj.steps), abs=1e-6)


def test_newer_schema_is_refused():
    text = run([{"type": "finish"}]).to_jsonl()
    header, rest = text.split("\n", 1)
    bumped = json.loads(header) | {"schema_version": SCHEMA_VERSION + 1}
    with pytest.raises(TrajectoryVersionError):
        loads_trajectory(json.dumps(bumped) + "\n" + rest)


@pytest.mark.parametrize("text", ["", "not json\n", '{"kind": "step"}\n', '{"kind": "header", "schema_version": 0}\n',
                                  '{"kind": "header", "schema_version": "1"}\n'])
def test_malformed_files_are_rejected(text):
    with pytest.raises(TrajectoryFormatError):
        loads_trajectory(text)


def test_step_line_after_header_must_be_step():
    header = run([{"type": "finish"}]).to_jsonl().splitlines()[0]
    with pytest.raises(TrajectoryFormatError):
        loads_trajectory(header + '\n{"kind": "header"}\n')


def secret_task():
    return simple_task(files={"m.py": f'def f():\n    return 1\nKEY = "{SECRET}"\nOTHER = "{SECRET}"\n',
                              "tests/test_m.py": ""})


@pytest.mark.parametrize("drop", [False, True])
def test_planted_secret_never_appears_in_export(drop):
    traj = run([READ, {"type": "search", "query": "KEY"}, {"type": "create_file", "path": "k.py", "content": SECRET},
                {"type": "edit", "path": "m.py", "search": "x", "replace": SECRET}, {"type": "finish"}],
               task=secret_task())
    assert SECRET in json.dumps(traj.steps)  # the raw action text still holds it ...
    assert SECRET not in traj.to_jsonl(drop_file_contents=drop)  # ... but never the export
    assert SECRET not in json.dumps(export_dataset([traj], "failed", drop_file_contents=drop))


def test_drop_file_contents_removes_file_text_but_keeps_structure():
    traj = run([READ, {"type": "create_file", "path": "n.py", "content": "secret body"}, {"type": "finish"}])
    out = redact(traj, drop_file_contents=True)
    assert out.steps[0]["result"]["output"].startswith("<dropped:")
    assert out.steps[1]["action"]["content"].startswith("<dropped:") and out.steps[1]["action"]["path"] == "n.py"
    assert "secret body" not in out.to_jsonl(drop_file_contents=True)
    assert "def f()" not in out.to_jsonl(drop_file_contents=True)
    assert "def f()" in traj.to_jsonl()  # default keeps contents
    assert traj.steps[0]["result"]["output"].startswith("def f()")  # redact() does not mutate its input


def good(extra=0, seed=0):
    return run([READ] * extra + [FIX, {"type": "finish"}], seed=seed)


def bad(seed=0, actions=None):
    return run(actions or [{"type": "finish"}], seed=seed)


def test_dataset_kinds():
    ok, ko = good(), bad()
    assert [r["success"] for r in export_dataset([ko, ok], "successful")] == [True]
    assert [r["success"] for r in export_dataset([ko, ok], "failed")] == [False]
    rec = export_dataset([ok], "successful")[0]
    assert set(rec) == {"trajectory_id", "task_id", "seed", "total_reward", "success", "steps"}
    assert set(rec["steps"][0]) == {"observation", "action"}
    with pytest.raises(ValueError, match="unknown dataset kind"):
        export_dataset([ok], "other")


def test_paired_dataset_is_deterministic_and_prefers_best_success_and_hardest_failure():
    pool = [good(0, 0), good(3, 1), bad(0), bad(2, [READ, {"type": "finish"}]), good(1, 2)]
    expected = export_dataset(pool, "paired")
    for seed in range(5):
        shuffled = pool[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311
        assert export_dataset(shuffled, "paired") == expected
    (pair,) = expected
    assert pair["task_id"] == "unit-simple"
    assert len(pair["chosen"]["steps"]) == 2 and pair["chosen"]["success"] is True  # fewest steps wins the tie
    assert pair["rejected"]["success"] is False and len(pair["rejected"]["steps"]) == 1  # highest reward failure


def test_pairing_needs_both_outcomes_on_the_same_task():
    assert export_dataset([good(), good(1)], "paired") == []
    assert export_dataset([bad()], "paired") == []
    other = simple_task(id="unit-other")
    env = PatchQuestEnv(tasks=[other])
    loser = rollout(env, ListPolicy([]), task_id="unit-other")
    env.close()
    assert export_dataset([good(), loser], "paired") == []


def test_oracle_trajectory_ids_are_reproducible():
    task = simple_task()
    ids = []
    for _ in range(2):
        env = PatchQuestEnv(tasks=[task])
        ids.append(rollout(env, OraclePolicy([task]), task_id=task.id).trajectory_id)
        env.close()
    assert ids[0] == ids[1]

import json
import os

import pytest

from patchquest.evaluation.tasks import load_corpus
from patchquest.rl.env import EnvConfig, EpisodeError, GymAdapter, PatchQuestEnv
from tests.unit.rl.helpers import FIX, ORACLE_MARKER, simple_task, snapshot


@pytest.fixture
def env():
    e = PatchQuestEnv(tasks=[simple_task()])
    yield e
    e.close()


def act(env, **action):
    return env.step(action)


def test_reset_is_deterministic():
    runs = []
    for _ in range(2):
        e = PatchQuestEnv()
        obs, info = e.reset(seed=5)
        runs.append((json.dumps(obs, sort_keys=True), info, snapshot(e.workspace_path)))
        e.close()
    assert runs[0] == runs[1]
    assert runs[0][2]  # a non-empty workspace


def test_seed_selects_task_and_task_id_overrides():
    e = PatchQuestEnv()
    ids = [t.id for t in e.tasks]
    assert e.reset(seed=1)[1]["task_id"] == ids[1]
    assert e.reset(seed=len(ids) + 1)[1]["task_id"] == ids[1]
    assert e.reset(seed=1, task_id=ids[0])[1]["task_id"] == ids[0]
    with pytest.raises(ValueError, match="unknown task"):
        e.reset(task_id="nope")
    e.close()


def test_step_requires_active_episode(env):
    with pytest.raises(EpisodeError):
        env.step({"type": "finish"})
    env.reset()
    act(env, type="finish")
    with pytest.raises(EpisodeError):
        act(env, type="finish")
    env.close()
    with pytest.raises(EpisodeError):
        env.workspace_path  # noqa: B018


def test_observation_shape(env):
    obs, _ = env.reset()
    assert obs == {"task_id": "unit-simple", "instruction": "Make f() return 2.",
                   "file_tree": ["m.py", "tests/test_m.py"], "last_action": None, "steps_left": 30}


def test_read_list_search(env):
    env.reset()
    obs = act(env, type="read_file", path="m.py")[0]
    assert obs["last_action"] == {"type": "read_file", "ok": True, "output": "def f():\n    return 1\n",
                                  "truncated": False}
    assert act(env, type="list_dir", path=".")[0]["last_action"]["output"] == "m.py\ntests/"
    assert act(env, type="search", query="return")[0]["last_action"]["output"] == "m.py:2: return 1"
    assert act(env, type="search", query="zzz")[0]["last_action"]["output"] == "no matches"
    assert act(env, type="read_file", path="missing.py")[0]["last_action"]["ok"] is False
    assert act(env, type="list_dir", path="m.py")[0]["last_action"]["ok"] is False


def test_edit_uses_verified_engine_uniqueness_rules(env):
    env.reset()
    act(env, type="create_file", path="dup.py", content="x = 1\nx = 1\n")
    obs = act(env, type="edit", path="dup.py", search="x = 1", replace="x = 2")[0]
    assert obs["last_action"]["ok"] is False and "matches 2 places" in obs["last_action"]["output"]
    assert (env.workspace_path / "dup.py").read_text() == "x = 1\nx = 1\n"
    assert act(env, type="edit", path="m.py", search="absent", replace="y")[0]["last_action"]["ok"] is False
    assert act(env, type="edit", path="m.py", search="", replace="y")[0]["last_action"]["ok"] is False
    assert act(env, **FIX)[0]["last_action"]["ok"] is True
    assert (env.workspace_path / "m.py").read_text() == "def f():\n    return 2\n"


def test_create_file_refuses_overwrite_and_unsafe_paths(env):
    env.reset()
    assert act(env, type="create_file", path="new/a.txt", content="hi")[0]["file_tree"][-3:] == [
        "m.py", "new/a.txt", "tests/test_m.py"]
    for path in ("m.py", "../out.txt", "/tmp/out.txt", ".git/hooks/x", "a/../../b"):
        assert act(env, type="create_file", path=path, content="x")[0]["last_action"]["ok"] is False, path
    assert not (env.workspace_path.parent / "out.txt").exists()


def test_step_cap_truncates_and_scores_oracle():
    e = PatchQuestEnv(tasks=[simple_task()], config=EnvConfig(max_steps=3))
    e.reset()
    act(e, **FIX)
    _, _, terminated, truncated, info = act(e, type="list_dir", path=".")
    assert (terminated, truncated) == (False, False) and "oracle_passed" not in info
    obs, _, terminated, truncated, info = act(e, type="search", query="f")
    assert (terminated, truncated) == (False, True) and obs["steps_left"] == 0
    assert info["oracle_passed"] is True and info["success"] is True
    e.close()


def test_finish_terminates_without_truncation(env):
    env.reset()
    _, _, terminated, truncated, info = act(env, type="finish")
    assert (terminated, truncated) == (True, False) and info["success"] is False


def test_output_limit_truncates_observation():
    e = PatchQuestEnv(tasks=[simple_task()], config=EnvConfig(max_output_bytes=40))
    e.reset()
    act(e, type="create_file", path="big.txt", content="line\n" * 100)
    last = act(e, type="read_file", path="big.txt")[0]["last_action"]
    assert last["truncated"] is True and len(last["output"].encode()) <= 40
    e.close()


def test_command_time_limit_kills_tests():
    sleeper = "import time, unittest\n\nclass T(unittest.TestCase):\n    def test_s(self):\n        time.sleep(30)\n"
    task = simple_task(files={"tests/test_s.py": sleeper})
    e = PatchQuestEnv(tasks=[task], config=EnvConfig(max_command_seconds=0.5))
    e.reset()
    last = act(e, type="run_tests")[0]["last_action"]
    assert last["ok"] is False and "timed out" in last["output"]
    e.close()


def test_run_tests_output_is_reproducible_and_portable(env):
    env.reset()
    out1 = act(env, type="run_tests")[0]["last_action"]["output"]
    out2 = act(env, type="run_tests")[0]["last_action"]["output"]
    assert out1 == out2 and "FAILED (failures=1)" in out1
    assert str(env.workspace_path) not in out1


def test_policy_blocked_command_is_not_run_and_penalised():
    e = PatchQuestEnv(tasks=[simple_task(test_command="curl http://example.invalid/x")])
    e.reset()
    obs, reward, _, _, info = act(e, type="run_tests")
    assert "blocked by policy" in obs["last_action"]["output"]
    assert info["reward_breakdown"].safety == -0.5 and info["counters"]["blocked_commands"] == 1
    assert reward == pytest.approx(-0.51)
    e.close()


def test_secret_files_and_secrets_are_hidden_from_observations(tmp_path):
    secret = "sk-" + "a1B2c3D4e5" * 3
    task = simple_task(files={"m.py": f'KEY = "{secret}"\n', "tests/test_m.py": "", ".env": "TOKEN=abc\n"})
    e = PatchQuestEnv(tasks=[task])
    obs, _ = e.reset()
    assert ".env" not in obs["file_tree"]
    assert secret not in json.dumps(act(e, type="read_file", path="m.py")[0])
    assert secret not in json.dumps(act(e, type="search", query="KEY")[0])
    assert act(e, type="read_file", path=".env")[0]["last_action"]["ok"] is False
    e.close()


def test_close_removes_workspace_and_reset_replaces_it(env):
    env.reset()
    first = env.workspace_path
    env.reset()
    second = env.workspace_path
    assert not first.exists() and second.exists()
    env.close()
    assert not second.exists()


def test_gym_adapter_shape():
    adapter = GymAdapter(PatchQuestEnv(tasks=[simple_task()]))
    obs, info = adapter.reset(seed=0, options={"task_id": "unit-simple"})
    assert set(obs) == set(adapter.observation_space) and info["task_id"] == "unit-simple"
    result = adapter.step({"type": "finish"})
    assert len(result) == 5 and result[2] is True
    assert set(adapter.action_space["oneof"]) >= {"read_file", "edit", "finish"}
    adapter.close()


def test_workspace_is_a_shadow_copy_of_visible_files_only(env):
    env.reset()
    assert sorted(snapshot(env.workspace_path)) == ["m.py", "tests/test_m.py"]
    assert os.path.isdir(env.workspace_path)
    assert ORACLE_MARKER not in json.dumps(snapshot(env.workspace_path))


def test_corpus_tasks_all_loadable_by_env():
    assert len(PatchQuestEnv().tasks) == len(load_corpus())

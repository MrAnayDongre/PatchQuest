"""The hidden oracle must be unreachable through every action."""

import json
import os

import pytest

from patchquest.rl.env import PatchQuestEnv
from patchquest.rl.policies import OraclePolicy, rollout
from tests.unit.rl.helpers import ORACLE_MARKER, simple_task

ORACLE_PATH = "tests/test_oracle.py"


@pytest.fixture
def env():
    e = PatchQuestEnv(tasks=[simple_task()])
    e.reset()
    yield e
    e.close()


def last(env, **action):
    return env.step(action)[0]["last_action"]


def test_oracle_file_is_not_in_workspace_tree_or_listing(env):
    assert not (env.workspace_path / ORACLE_PATH).exists()
    assert ORACLE_PATH not in env.step({"type": "list_dir", "path": "tests"})[0]["file_tree"]
    assert "oracle" not in last(env, type="list_dir", path="tests")["output"]
    assert last(env, type="read_file", path=ORACLE_PATH)["ok"] is False  # guessing the file name


@pytest.mark.parametrize("query", [ORACLE_MARKER, "test_oracle", "class O(", "hidden"])
def test_search_cannot_hit_oracle(env, query):
    assert last(env, type="search", query=query)["output"] == "no matches"


@pytest.mark.parametrize("path", ["../oracle/tests/test_oracle.py", "../../source/m.py", "tests/../../x",
                                  "/etc/passwd", "~/x", "..", "tests/../.."])
def test_traversal_is_rejected(env, path):
    for action in ({"type": "read_file", "path": path}, {"type": "list_dir", "path": path}):
        assert env.step(action)[0]["last_action"]["ok"] is False


def test_symlinks_cannot_escape(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "oracle.txt").write_text(ORACLE_MARKER)
    ws = env.workspace_path
    os.symlink(outside / "oracle.txt", ws / "link_file")
    os.symlink(outside, ws / "link_dir")
    os.symlink("m.py", ws / "link_inside")
    assert last(env, type="read_file", path="link_file")["ok"] is False
    assert last(env, type="read_file", path="link_dir/oracle.txt")["ok"] is False
    assert last(env, type="list_dir", path="link_dir")["ok"] is False
    assert last(env, type="create_file", path="link_dir/new.txt", content="x")["ok"] is False
    assert last(env, type="edit", path="link_file", search="a", replace="b")["ok"] is False
    assert not (outside / "new.txt").exists()
    assert last(env, type="search", query=ORACLE_MARKER)["output"] == "no matches"
    tree = env.step({"type": "list_dir", "path": "."})[0]["file_tree"]
    assert not {"link_file", "link_dir", "link_inside"} & set(tree)


def test_planting_oracle_named_file_cannot_game_the_oracle(env):
    env.step({"type": "create_file", "path": ORACLE_PATH, "content": "import unittest\n"})
    *_, info = env.step({"type": "finish"})
    assert info["oracle_passed"] is False  # the real oracle overwrote the planted file in the scratch copy
    assert ORACLE_MARKER not in (env.workspace_path / ORACLE_PATH).read_text()


def test_oracle_policy_transcript_never_contains_oracle_text():
    task = simple_task()
    env = PatchQuestEnv(tasks=[task])
    traj = rollout(env, OraclePolicy([task]), task_id=task.id)
    env.close()
    assert traj.success
    assert ORACLE_MARKER not in json.dumps(traj.steps)

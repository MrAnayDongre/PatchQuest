import pytest

from patchquest.rl.actions import ACTION_SCHEMAS, InvalidAction, action_to_dict, parse_action
from patchquest.rl.env import PatchQuestEnv
from tests.unit.rl.helpers import simple_task

VALID = [
    {"type": "read_file", "path": "m.py"}, {"type": "list_dir", "path": "."}, {"type": "search", "query": "f"},
    {"type": "edit", "path": "m.py", "search": "a", "replace": "b"},
    {"type": "create_file", "path": "n.py", "content": ""}, {"type": "run_tests"}, {"type": "finish"},
]
INVALID = [
    None, "finish", 3, [], {}, {"type": "rm"}, {"type": 1}, {"type": "read_file"}, {"type": "read_file", "path": 1},
    {"type": "read_file", "path": "a", "extra": "b"}, {"type": "run_tests", "path": "a"},
    {"type": "read_file", "path": "x" * 2000}, {"type": "create_file", "path": "a", "content": "x" * 200_000},
]


def test_schema_covers_every_valid_action():
    assert {a["type"] for a in VALID} == set(ACTION_SCHEMAS)


@pytest.mark.parametrize("raw", VALID)
def test_valid_actions_round_trip(raw):
    assert action_to_dict(parse_action(raw)) == raw
    assert parse_action(parse_action(raw)) == parse_action(raw)


@pytest.mark.parametrize("raw", INVALID)
def test_invalid_actions_are_rejected(raw):
    with pytest.raises(InvalidAction):
        parse_action(raw)


@pytest.mark.parametrize("raw", INVALID)
def test_env_turns_invalid_actions_into_observations(raw):
    env = PatchQuestEnv(tasks=[simple_task()])
    env.reset()
    obs, reward, terminated, truncated, info = env.step(raw)
    env.close()
    assert info["invalid"] is True and not terminated and not truncated
    assert obs["last_action"]["type"] == "invalid" and obs["last_action"]["ok"] is False
    assert reward == pytest.approx(-0.06)
    assert info["counters"]["invalid_actions"] == 1

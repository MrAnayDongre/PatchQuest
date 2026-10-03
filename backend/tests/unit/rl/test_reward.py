"""Each reward component alone, then combined. Task: two visible tests (one fails), hidden oracle needs f() == 2."""

import pytest

from patchquest.rl.env import PatchQuestEnv
from patchquest.rl.reward import RewardBreakdown, RewardConfig
from tests.unit.rl.helpers import FIX, simple_task

CFG = RewardConfig()


@pytest.fixture
def env():
    e = PatchQuestEnv(tasks=[simple_task()])
    e.reset()
    yield e
    e.close()


def step(env, **action):
    _, reward, _, _, info = env.step(action)
    b = info["reward_breakdown"]
    assert reward == pytest.approx(b.total)
    return b, info


def only(b: RewardBreakdown, **expected: float) -> None:
    """Assert the named components, and that every other component is exactly zero."""
    for name in b.to_dict():
        if name != "total":
            assert getattr(b, name) == pytest.approx(expected.get(name, 0.0)), name


def test_breakdown_total_is_sum_of_components():
    b = RewardBreakdown(oracle=1, test_fraction=0.1, patch_size=-0.2, step_cost=-0.01, safety=-0.5, invalid=-0.05)
    assert b.total == pytest.approx(0.34) and b.to_dict()["total"] == b.total


def test_step_cost_only_on_plain_step(env):
    only(step(env, type="read_file", path="m.py")[0], step_cost=-CFG.step_cost)


def test_invalid_action_penalty(env):
    only(step(env, type="bogus")[0], step_cost=-CFG.step_cost, invalid=-CFG.invalid_action)


def test_failed_but_valid_action_is_not_invalid(env):
    only(step(env, type="read_file", path="nope.py")[0], step_cost=-CFG.step_cost)


def test_test_fraction_delta_tracks_progress_from_pristine_baseline(env):
    only(step(env, type="run_tests")[0], step_cost=-0.01)  # baseline 0.5, nothing changed: delta 0
    step(env, **FIX)
    only(step(env, type="run_tests")[0], step_cost=-0.01, test_fraction=CFG.test_fraction * 0.5)
    only(step(env, type="run_tests")[0], step_cost=-0.01)  # no further progress


def test_baseline_is_taken_before_first_mutation(env):
    step(env, **FIX)  # mutation first; the pristine fraction (0.5) must still be the reference
    only(step(env, type="run_tests")[0], step_cost=-0.01, test_fraction=CFG.test_fraction * 0.5)


def test_regression_gives_negative_test_fraction(env):
    step(env, type="edit", path="m.py", search="return 1", replace="return -1")
    only(step(env, type="run_tests")[0], step_cost=-0.01, test_fraction=-CFG.test_fraction * 0.5)


def test_oracle_only_at_end_and_only_on_success(env):
    step(env, **FIX)
    b, info = step(env, type="run_tests")
    assert b.oracle == 0 and "oracle_passed" not in info
    b, info = step(env, type="finish")
    assert b.oracle == CFG.oracle_success and info["success"] is True


def test_failing_patch_gets_no_oracle_reward(env):
    b, info = step(env, type="finish")
    assert b.oracle == 0 and info["oracle_passed"] is False


def test_patch_size_penalty_counts_changed_lines_once(env):
    step(env, type="edit", path="m.py", search="return 1", replace="return 3")
    step(env, **{**FIX, "search": "return 3"})  # re-editing the same line must not be charged twice
    b, info = step(env, type="finish")
    assert info["changed_lines"] == 2
    assert b.patch_size == pytest.approx(-2 * CFG.patch_size_per_line)


def test_patch_size_penalty_is_capped(env):
    step(env, type="create_file", path="big.txt", content="x\n" * 5000)
    assert step(env, type="finish")[0].patch_size == -CFG.patch_size_cap


def test_forbidden_change_penalised_and_blocks_success():
    e = PatchQuestEnv(tasks=[simple_task(forbid_changes=("m.py",))])
    e.reset()
    step(e, **FIX)
    b, info = step(e, type="finish")
    assert b.oracle == CFG.oracle_success and b.safety == -CFG.safety_violation
    assert info["forbidden_change"] is True and info["success"] is False
    e.close()


def test_untouched_forbidden_file_is_not_penalised():
    e = PatchQuestEnv(tasks=[simple_task(forbid_changes=("tests/test_m.py",))])
    e.reset()
    step(e, **FIX)
    assert step(e, type="finish")[0].safety == 0
    e.close()


def test_combined_episode_matches_hand_computed_total():
    e = PatchQuestEnv(tasks=[simple_task()])
    e.reset()
    total = 0.0
    for action in (dict(type="read_file", path="m.py"), dict(type="bogus"), FIX, dict(type="run_tests"),
                   dict(type="finish")):
        total += e.step(action)[1]
    expected = (-5 * CFG.step_cost - CFG.invalid_action + CFG.test_fraction * 0.5
                - 2 * CFG.patch_size_per_line + CFG.oracle_success)
    assert total == pytest.approx(expected)
    e.close()


def test_custom_weights_are_honoured():
    e = PatchQuestEnv(tasks=[simple_task()], reward=RewardConfig(step_cost=0.5, oracle_success=10.0))
    e.reset()
    e.step(FIX)
    b = e.step({"type": "finish"})[4]["reward_breakdown"]
    assert b.step_cost == -0.5 and b.oracle == 10.0
    e.close()


def test_reward_config_is_frozen():
    with pytest.raises(AttributeError):
        CFG.step_cost = 1  # type: ignore[misc]

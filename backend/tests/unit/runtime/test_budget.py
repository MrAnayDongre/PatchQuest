from types import SimpleNamespace

from patchquest.config import AgentConfig
from patchquest.domain.budget import BudgetKind, BudgetLine, exhausted, snapshot


def counters(**kw):
    base = {"model_calls": 0, "tokens_used": 0, "wall_seconds": 0.0, "patch_attempts": 0, "repair_rounds": 0,
            "retries": 0, "commands_run": []}
    return SimpleNamespace(**{**base, **kw})


def test_line_semantics():
    assert BudgetLine(BudgetKind.TOKENS, 5, 0).unlimited and not BudgetLine(BudgetKind.TOKENS, 10**9, 0).exhausted
    line = BudgetLine(BudgetKind.MODEL_CALLS, 10, 10)
    assert line.exhausted and line.fraction == 1.0 and line.to_dict()["remaining"] == 0
    assert BudgetLine(BudgetKind.MODEL_CALLS, 3, 12).fraction == 0.25


def test_snapshot_covers_every_kind_once_in_a_stable_order():
    lines = snapshot(counters(), AgentConfig())
    assert [line.kind for line in lines] == list(BudgetKind)


def test_snapshot_reflects_counters_and_config():
    cfg = AgentConfig(max_model_calls=5, max_commands=2)
    lines = {line.kind: line for line in snapshot(counters(model_calls=5, commands_run=[1, 2, 3]), cfg)}
    assert lines[BudgetKind.MODEL_CALLS].exhausted and lines[BudgetKind.COMMANDS].used == 3
    assert not lines[BudgetKind.TOKENS].exhausted  # unlimited by default


def test_exhausted_only_considers_the_kinds_about_to_be_spent():
    cfg = AgentConfig(max_commands=1, max_model_calls=1)
    c = counters(model_calls=1, commands_run=[1])
    assert exhausted(c, cfg, among=(BudgetKind.COMMANDS,)).kind is BudgetKind.COMMANDS
    assert exhausted(c, cfg, among=(BudgetKind.WALL_TIME,)) is None

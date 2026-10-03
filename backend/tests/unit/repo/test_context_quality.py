"""The context-quality harness itself, and the floors the shipped strategies must keep."""

from __future__ import annotations

import pytest

from patchquest.config import AgentConfig, validate_overrides
from patchquest.context.builder import STRATEGIES, build_context
from patchquest.evaluation.context_fixture import CASES, FILES, Case
from patchquest.evaluation.context_quality import evaluate, score
from patchquest.memory.repo_indexer import index_repo
from patchquest.memory.repo_map import get_repo_map


@pytest.fixture(scope="module")
def report():
    # the module-scoped call cannot use the per-test database fixture, so evaluate() isolates itself
    return evaluate(repetitions=2)


def test_every_case_names_files_that_exist_in_the_fixture():
    for case in CASES:
        assert case.relevant_files <= FILES.keys(), case.id
        assert all(path in case.relevant_files and name in FILES[path] for path, name in case.relevant_symbols), case.id
    assert len({c.id for c in CASES}) == len(CASES)


def test_scoring_is_exact_on_a_hand_checked_case():
    from patchquest.context.builder import ContextItem

    case = Case("x", "t", frozenset({"a.py", "b.py"}), frozenset({("a.py", "fa"), ("b.py", "fb")}))
    items = [ContextItem("a.py", content="def fa(): pass", chars=100), ContextItem("noise.py", content="x", chars=300)]
    s = score(case, items)
    assert (s["relevant_file_recall"], s["relevant_symbol_recall"], s["file_precision"], s["irrelevant_context_ratio"]) == (0.5, 0.5, 0.5, 0.75)
    assert s["missed"] == ["b.py"] and s["tokens"] == 100.0 and s["tokens_per_relevant_file"] == 100.0


def test_the_shipped_strategies_keep_their_measured_floors(report):
    lexical, focused = report["strategies"]["lexical"], report["strategies"]["focused"]
    assert lexical["relevant_file_recall"] >= 0.85 and lexical["relevant_symbol_recall"] >= 0.85
    assert focused["relevant_file_recall"] >= lexical["relevant_file_recall"] - 0.01  # focusing must not cost recall here
    assert focused["file_precision"] > lexical["file_precision"] + 0.05
    assert focused["irrelevant_context_ratio"] < lexical["irrelevant_context_ratio"]
    assert focused["mean_tokens"] < lexical["mean_tokens"]
    assert focused["recall_vs_lexical"]["worse"] == 0


def test_the_semantic_gap_case_is_reported_as_the_miss_it_is(report):
    decimals = next(r for r in report["strategies"]["lexical"]["per_case"] if r["id"] == "decimals")
    assert decimals["relevant_file_recall"] == 0.0 and decimals["missed"] == ["utils/money.py"]  # no shared words: lexical selection cannot find it


def test_incremental_indexing_reuses_work_and_leaves_no_stale_entries(report):
    idx = report["index"]
    assert idx["unchanged"]["files_reprocessed"] == 0 and idx["unchanged"]["cache_hit_rate"] == 1.0
    assert idx["incremental"]["files_reprocessed"] == 5 and idx["incremental"]["files_removed"] == 2 and idx["incremental"]["cache_hit_rate"] > 0.85
    assert idx["stale_index_rate"]["before_refresh"] > 0 and idx["stale_index_rate"]["after_refresh"] == 0.0


def test_unknown_strategies_are_refused_everywhere(tmp_path):
    with pytest.raises(ValueError, match="unknown strategy"):
        evaluate(("telepathy",))
    with pytest.raises(ValueError, match="unknown context strategy"):
        build_context(str(tmp_path), "t", strategy="telepathy")
    with pytest.raises(ValueError):
        validate_overrides({"agent.context_strategy": "telepathy"})
    assert validate_overrides({"agent.context_strategy": "focused"}) == {"agent.context_strategy": "focused"}
    assert AgentConfig().context_strategy == "lexical" and set(STRATEGIES) == {"lexical", "focused"}


def test_explicit_evidence_survives_focusing(tmp_path):
    from patchquest.evaluation.context_fixture import build

    repo = build(tmp_path / "r")
    index_repo(repo)
    items = build_context(repo, "In billing/pdf.py change the footer", get_repo_map(repo), strategy="focused")
    assert items[0].path == "billing/pdf.py" and "user_reference" in items[0].reasons
    traced = build_context(repo, "crash", get_repo_map(repo), failure_text='File "payments/fees.py", line 2, in compute_fee', strategy="focused")
    assert traced[0].path == "payments/fees.py"

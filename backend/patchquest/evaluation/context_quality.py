"""Does context selection find what a person would need? A model-free, repeatable measurement.

Each case in ``context_fixture`` states which files (and which symbols in them) matter for a task. A strategy turns
(repository, task) into a list of files to show a model; this scores it:

* **relevant_file_recall**     relevant files selected / relevant files.
* **relevant_symbol_recall**   relevant symbols whose definition appears in the selected text / relevant symbols.
* **file_precision**           relevant files selected / files selected.
* **irrelevant_context_ratio** characters of irrelevant files / characters selected (what the model reads for nothing).
* **tokens**                   selected characters / 4 (the budgeting estimate); ``tokens_per_relevant_file`` divides by hits.
* **build_latency_ms**         wall time of one selection, p50 and p95 over repetitions (index lookup + file reads).

And the index itself: cold index time, an unchanged re-index, an incremental re-index after edits/deletes/additions, how
much work each did, and the **stale index rate** - the share of indexed files that no longer match the disk - before and
after the incremental refresh. Everything runs in a throwaway database (``isolated_sqlite``).
"""

from __future__ import annotations

import os
import platform
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from patchquest.context.builder import CHARS_PER_TOKEN, ContextItem, build_context
from patchquest.database import get_db, isolated_sqlite
from patchquest.evaluation.context_fixture import CASES, Case
from patchquest.evaluation.context_fixture import build as build_fixture
from patchquest.memory.repo_indexer import _hash_file, index_repo
from patchquest.memory.repo_map import get_repo_map

Strategy = Callable[[str, Case, dict], list[ContextItem]]


def lexical(repo: str, case: Case, repo_map: dict) -> list[ContextItem]:
    """The shipped strategy: named files, planned files, tracebacks, path and symbol evidence, related tests."""
    return build_context(repo, case.task, repo_map, list(case.planned_files), failure_text=case.failure_text)


def focused(repo: str, case: Case, repo_map: dict) -> list[ContextItem]:
    return build_context(repo, case.task, repo_map, list(case.planned_files), failure_text=case.failure_text, strategy="focused")


STRATEGIES: dict[str, Strategy] = {"lexical": lexical, "focused": focused}


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(p / 100 * len(ordered)) - 1))]


def score(case: Case, items: list[ContextItem]) -> dict[str, Any]:
    selected = {i.path: i for i in items}
    hits = case.relevant_files & selected.keys()
    symbols_found = sum(1 for path, name in case.relevant_symbols if path in selected and name in selected[path].content)
    chars = sum(i.chars for i in items)
    irrelevant = sum(i.chars for i in items if i.path not in case.relevant_files)
    return {
        "id": case.id, "selected": sorted(selected), "missed": sorted(case.relevant_files - selected.keys()),
        "relevant_file_recall": len(hits) / len(case.relevant_files),
        "relevant_symbol_recall": symbols_found / len(case.relevant_symbols) if case.relevant_symbols else None,
        "file_precision": len(hits) / len(selected) if selected else 0.0,
        "irrelevant_context_ratio": irrelevant / chars if chars else 0.0,
        "tokens": round(chars / CHARS_PER_TOKEN, 1),
        "tokens_per_relevant_file": round(chars / CHARS_PER_TOKEN / len(hits), 1) if hits else None,
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [r[key] for r in rows if r[key] is not None]
    return round(statistics.fmean(values), 4) if values else None


def _stale_rate(repo: str) -> float:
    """Indexed files that are missing from disk or whose content differs, over indexed files."""
    files = get_repo_map(repo)["files"]
    with get_db() as conn:
        hashes = {r["file_path"]: r["file_hash"] for r in conn.execute("SELECT file_path, file_hash FROM repo_files WHERE repo_path = ?", (repo,))}
    stale = sum(1 for f in files if not (Path(repo) / f["file_path"]).is_file() or _hash_file(Path(repo) / f["file_path"]) != hashes[f["file_path"]])
    return round(stale / len(files), 4) if files else 0.0


def _index_experiment(repo: str) -> dict[str, Any]:
    root = Path(repo)
    for p in root.rglob("*"):  # make every file look old, so timestamps are trusted like they are in a real checkout
        if p.is_file():
            os.utime(p, (time.time() - 600, time.time() - 600))
    with get_db() as conn:
        conn.execute("DELETE FROM repo_files WHERE repo_path = ?", (repo,))
        conn.execute("DELETE FROM repo_symbols WHERE repo_path = ?", (repo,))
    cold = index_repo(repo)
    warm = index_repo(repo)
    edits = ["payments/fees.py", "billing/taxes.py", "utils/money.py"]
    for rel in edits:
        path = root / rel
        path.write_text(path.read_text() + "\n\ndef added_" + Path(rel).stem + "():\n    return 1\n")
        os.utime(path, (time.time() - 300, time.time() - 300))
    (root / "utils/dates.py").unlink()
    (root / "users/emails.py").unlink()
    for rel in ("payments/chargebacks.py", "billing/credits.py"):
        (root / rel).write_text("def " + Path(rel).stem + "():\n    return 0\n")
        os.utime(root / rel, (time.time() - 300, time.time() - 300))
    stale_before = _stale_rate(repo)
    incremental = index_repo(repo)
    stale_after = _stale_rate(repo)
    return {
        "files": cold["files_indexed"], "symbols": cold["symbols_indexed"],
        "cold": {k: cold[k] for k in ("elapsed_ms", "files_reprocessed", "symbols_reprocessed", "cache_hit_rate")},
        "unchanged": {k: warm[k] for k in ("elapsed_ms", "files_reprocessed", "symbols_reprocessed", "cache_hit_rate")},
        "incremental": {k: incremental[k] for k in ("elapsed_ms", "files_reprocessed", "files_removed", "symbols_reprocessed", "cache_hit_rate")},
        "changes_applied": {"edited": len(edits), "deleted": 2, "added": 2},
        "stale_index_rate": {"before_refresh": stale_before, "after_refresh": stale_after},
    }


def evaluate(strategies: tuple[str, ...] = ("lexical", "focused"), repetitions: int = 7) -> dict[str, Any]:
    unknown = set(strategies) - STRATEGIES.keys()
    if unknown:
        raise ValueError(f"unknown strategy: {', '.join(sorted(unknown))} (known: {', '.join(STRATEGIES)})")
    with tempfile.TemporaryDirectory(prefix="pq-ctx-") as tmp, isolated_sqlite(Path(tmp) / "ctx.db"):
        repo = build_fixture(Path(tmp) / "repo")
        index_repo(repo)
        repo_map = get_repo_map(repo)
        report: dict[str, Any] = {"schema": 1, "cases": len(CASES), "fixture_files": len(repo_map["files"]), "strategies": {},
                                  "environment": {"python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count(),
                                                  "repetitions": repetitions}}
        for name in strategies:
            rows, latencies = [], []
            for case in CASES:
                timings = []
                items: list[ContextItem] = []
                for _ in range(repetitions):
                    started = time.perf_counter()
                    items = STRATEGIES[name](repo, case, repo_map)
                    timings.append((time.perf_counter() - started) * 1000)
                latencies += timings
                rows.append({**score(case, items), "latency_ms": round(statistics.median(timings), 3)})
            report["strategies"][name] = {
                "relevant_file_recall": _mean(rows, "relevant_file_recall"), "relevant_symbol_recall": _mean(rows, "relevant_symbol_recall"),
                "file_precision": _mean(rows, "file_precision"), "irrelevant_context_ratio": _mean(rows, "irrelevant_context_ratio"),
                "mean_tokens": _mean(rows, "tokens"), "tokens_per_relevant_file": _mean(rows, "tokens_per_relevant_file"),
                "build_latency_ms": {"p50": round(_percentile(latencies, 50), 3), "p95": round(_percentile(latencies, 95), 3)},
                "per_case": rows}
        if len(strategies) >= 2:  # paired, per case, against the first strategy
            base = {r["id"]: r for r in report["strategies"][strategies[0]]["per_case"]}
            for name in strategies[1:]:
                better = same = worse = 0
                for row in report["strategies"][name]["per_case"]:
                    delta = row["relevant_file_recall"] - base[row["id"]]["relevant_file_recall"]
                    better, same, worse = (better + 1, same, worse) if delta > 1e-9 else (better, same, worse + 1) if delta < -1e-9 else (better, same + 1, worse)
                report["strategies"][name]["recall_vs_" + strategies[0]] = {"better": better, "same": same, "worse": worse}
        report["index"] = _index_experiment(repo)
    return report

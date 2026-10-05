"""Comparing models and settings on the same tasks, and saying honestly what the comparison can support.

``run_matrix``     the corpus against several provider/model targets (success, attribution, latency, tokens, calls).
``run_experiment`` one model, the same tasks, a *baseline* setting versus a *candidate* setting (``agent.*`` overrides),
                   paired task by task. With a small task count almost nothing is statistically significant; the exact
                   two-sided sign test is reported next to the wins and losses so no one reads a trend into noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Any

from patchquest.config import validate_overrides
from patchquest.evaluation.runner import run_eval


@dataclass(frozen=True)
class Target:
    provider: str
    model: str | None = None
    base_url: str | None = None

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider


def parse_target(text: str) -> Target:
    """``provider``, ``provider:model`` or ``provider:model@http://host/v1``."""
    base_url = None
    if "@" in text:
        text, base_url = text.split("@", 1)
    provider, _, model = text.partition(":")
    if not provider:
        raise ValueError(f"target '{text}' needs a provider, e.g. sglang:Qwen/Qwen3-0.6B@http://localhost:30000/v1")
    return Target(provider, model or None, base_url or None)


def sign_test_p(wins: int, losses: int) -> float | None:
    """Exact two-sided sign test on paired outcomes (ties excluded). None when there is nothing to test."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return round(min(1.0, 2 * tail), 4)


def _row(t: Target, report: dict[str, Any]) -> dict[str, Any]:
    s = report["summary"]
    tasks = report["tasks"]
    calls = [x["model_calls"] for x in tasks]
    return {"target": t.label, "success": s["overall"]["success"], "tasks": s["overall"]["tasks"],
            "success_rate": s["overall"]["success_rate"], "attribution": s.get("attribution", {}),
            "tokens": s["totals"]["tokens"], "model_calls": sum(calls), "model_ms": s["totals"]["model_ms"],
            "wall_s": s["totals"]["wall_s"], "median_wall_s": s["per_task_medians"]["wall_s"],
            "approvals_requested": sum(x["approvals_requested"] for x in tasks)}


async def run_matrix(targets: list[Target], *, corpus: str | None = None, only: str | None = None, time_limit: float = 300,
                     overrides: dict[str, Any] | None = None, progress=None) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for t in targets:
        report = (await run_eval(corpus=corpus, only=only, provider=t.provider, model=t.model, base_url=t.base_url,
                                 time_limit=time_limit, overrides=overrides)).to_dict()
        reports.append({"target": t.label, "report": report})
        if progress:
            progress(t, report)
    digests = {r["report"]["corpus_digest"] for r in reports}
    return {"kind": "matrix", "corpus_digest": digests.pop() if len(digests) == 1 else None,
            "table": [_row(t, r["report"]) for t, r in zip(targets, reports, strict=True)],
            "reports": [r["report"] for r in reports]}


async def run_experiment(*, baseline: dict[str, Any], candidate: dict[str, Any], target: Target, corpus: str | None = None,
                         only: str | None = None, time_limit: float = 300, progress=None) -> dict[str, Any]:
    """Run the same tasks twice, differing only in ``agent.*`` overrides, and pair the outcomes."""
    validate_overrides(baseline)
    validate_overrides(candidate)
    if baseline == candidate:
        raise ValueError("baseline and candidate are identical; there is nothing to compare")
    async def one(overrides: dict[str, Any]) -> dict[str, Any]:
        return (await run_eval(corpus=corpus, only=only, provider=target.provider, model=target.model, base_url=target.base_url,
                               time_limit=time_limit, overrides=overrides)).to_dict()

    a = await one(baseline)
    if progress:
        progress("baseline", a)
    b = await one(candidate)
    if progress:
        progress("candidate", b)
    by_a, by_b = {t["id"]: t for t in a["tasks"]}, {t["id"]: t for t in b["tasks"]}
    wins = [i for i in by_a if by_a[i]["status"] != "success" and by_b[i]["status"] == "success"]
    losses = [i for i in by_a if by_a[i]["status"] == "success" and by_b[i]["status"] != "success"]
    ties = len(by_a) - len(wins) - len(losses)
    delta = {"tokens": b["summary"]["totals"]["tokens"] - a["summary"]["totals"]["tokens"],
             "model_calls": b["summary"]["totals"]["model_calls"] - a["summary"]["totals"]["model_calls"],
             "wall_s": round(b["summary"]["totals"]["wall_s"] - a["summary"]["totals"]["wall_s"], 2)}
    n = len(by_a)
    return {
        "kind": "experiment", "target": target.label, "corpus_digest": a["corpus_digest"], "baseline_overrides": baseline,
        "candidate_overrides": candidate, "tasks": n,
        "baseline": _row(target, a), "candidate": _row(target, b),
        "paired": {"candidate_wins": wins, "candidate_losses": losses, "ties": ties, "sign_test_p": sign_test_p(len(wins), len(losses))},
        "delta": delta,
        "reading": (f"{n} tasks: {len(wins)} won, {len(losses)} lost, {ties} tied. "
                    + ("Too few decisive tasks to say anything; this is a measurement, not evidence of a difference."
                       if len(wins) + len(losses) < 6 else "Check the sign-test p-value before calling it a difference.")),
        "reports": {"baseline": a, "candidate": b},
    }

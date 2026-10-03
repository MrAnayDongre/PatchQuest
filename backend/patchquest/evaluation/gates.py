"""Regression gates, cheapest first. A tier passes only if every lower tier passed.

0  harness   the reference solutions all pass, and a *null* solution (no change at all) fails every task: the oracle is
             neither broken nor vacuous. CPU only, deterministic.
1  recovery  every crash / drift / corruption / cancel scenario ends correct and safe.
2  replay    each corpus run, replayed from its recorded model answers, matches the original (diff, verdict, phases).
3  smoke     (live model) three tasks: no harness, provider, sandbox or environment failures. Model failures are allowed.
4  corpus    (live model) the full corpus against a baseline result file: no task that succeeded may regress, and the
             success rate must meet ``fail_under``.

Tiers 3 and 4 need a real model and are skipped (reported as skipped, not passed) without a provider.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchquest.evaluation.metrics import compare
from patchquest.evaluation.recovery import run_recovery
from patchquest.evaluation.runner import isolated_environment, run_eval, run_task
from patchquest.evaluation.tasks import load_corpus
from patchquest.runtime.replay import ReplayMode, compare_runs

TIER_NAMES = {0: "harness", 1: "recovery", 2: "replay", 3: "live smoke", 4: "live corpus"}
LIVE_FAILURES = {"HARNESS_FAILURE", "PROVIDER_FAILURE", "SANDBOX_FAILURE", "ENVIRONMENT_FAILURE", "ORACLE_FAILURE", "TOOL_FAILURE"}
SMOKE_TASKS = ("bugfix-leap-year", "feature-slugify", "security-path-traversal")


@dataclass
class TierResult:
    tier: int
    name: str
    passed: bool
    skipped: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


async def tier0() -> TierResult:
    ref = (await run_eval(provider="scripted")).to_dict()
    null = (await run_eval(provider="scripted", scripted_mode="null")).to_dict()
    problems = [f"reference solution failed: {t['id']} ({t['failure_reason']}, {t['attribution']})" for t in ref["tasks"] if t["status"] != "success"]
    problems += [f"null solution passed (the oracle proves nothing): {t['id']}" for t in null["tasks"] if t["status"] == "success"]
    return TierResult(0, TIER_NAMES[0], not problems, details={
        "reference_success": ref["summary"]["overall"]["success"], "null_success": null["summary"]["overall"]["success"],
        "tasks": ref["summary"]["overall"]["tasks"]}, problems=problems)


async def tier1() -> TierResult:
    report = await run_recovery()
    bad = [f"{s['id']}: {'; '.join(s['failures'])}" for s in report["scenarios"] if not s["passed"]]
    return TierResult(1, TIER_NAMES[1], not bad, details=report["summary"], problems=bad)


async def tier2() -> TierResult:
    problems: list[str] = []
    checked = 0
    with isolated_environment() as (svc, repos):
        for task in load_corpus():
            result = await run_task(task, svc, repos, provider="scripted", model=None, base_url=None, time_limit=120)
            if result.run_id is None or result.outcome is None:
                problems.append(f"{task.id}: the original run did not finish")
                continue
            child = svc.replay(result.run_id, ReplayMode.MODEL)
            if not isinstance(child, dict):
                raise RuntimeError("model replay returns a run")
            await svc._tasks[child["id"]]
            comparison = compare_runs(result.run_id, child["id"])
            checked += 1
            if not comparison.matched:
                problems.append(f"{task.id}: replay differs in {', '.join(d.aspect for d in comparison.divergences)}")
    return TierResult(2, TIER_NAMES[2], not problems, details={"replayed": checked}, problems=problems)


async def tier3(provider: str, model: str | None, base_url: str | None, time_limit: float) -> TierResult:
    ids = [t.id for t in load_corpus() if t.id in SMOKE_TASKS]
    attrs: dict[str, int] = {}
    problems: list[str] = []
    ok = 0
    for task_id in ids:
        report = (await run_eval(only=task_id, provider=provider, model=model, base_url=base_url, time_limit=time_limit)).to_dict()
        t = report["tasks"][0]
        ok += t["status"] == "success"
        if t["attribution"]:
            attrs[t["attribution"]] = attrs.get(t["attribution"], 0) + 1
        if t["attribution"] in LIVE_FAILURES:
            problems.append(f"{task_id}: {t['attribution']} ({t['failure_kind'] or t['failure_reason']}) - not the model's fault")
    return TierResult(3, TIER_NAMES[3], not problems, details={"tasks": len(ids), "succeeded": ok, "attribution": attrs}, problems=problems)


async def tier4(provider: str, model: str | None, base_url: str | None, time_limit: float, baseline: str | None,
                fail_under: float) -> TierResult:
    report = (await run_eval(provider=provider, model=model, base_url=base_url, time_limit=time_limit)).to_dict()
    overall = report["summary"]["overall"]
    problems: list[str] = []
    details: dict[str, Any] = {"success": overall["success"], "tasks": overall["tasks"], "success_rate": overall["success_rate"],
                               "attribution": report["summary"].get("attribution", {})}
    if overall["success_rate"] < fail_under:
        problems.append(f"success rate {overall['success_rate']:.1%} is below the required {fail_under:.1%}")
    if baseline:
        diff = compare(json.loads(await asyncio.to_thread(Path(baseline).read_text)), report)
        details["regressions"], details["fixes"] = diff["regressions"], diff["fixes"]
        problems += [f"regression: {tid}" for tid in diff["regressions"]]
    return TierResult(4, TIER_NAMES[4], not problems, details=details, problems=problems)


async def run_gate(tier: int, *, provider: str | None = None, model: str | None = None, base_url: str | None = None,
                   baseline: str | None = None, fail_under: float = 0.0, time_limit: float = 300, progress=None) -> dict[str, Any]:
    if tier not in TIER_NAMES:
        raise ValueError(f"tier must be 0-4, got {tier}")
    results: list[TierResult] = []
    for n in range(tier + 1):
        if n >= 3 and not provider:
            result = TierResult(n, TIER_NAMES[n], passed=False, skipped=True, problems=["skipped: needs --provider (a live model)"])
        elif n == 0:
            result = await tier0()
        elif n == 1:
            result = await tier1()
        elif n == 2:
            result = await tier2()
        elif n == 3:
            result = await tier3(provider or "", model, base_url, time_limit)
        else:
            result = await tier4(provider or "", model, base_url, time_limit, baseline, fail_under)
        results.append(result)
        if progress:
            progress(result)
        if not result.passed and not result.skipped:
            break  # a failed cheap tier makes the expensive ones meaningless
    return {"tier_requested": tier, "passed": all(r.passed for r in results),  # a skipped tier is not a passed tier
            "highest_passed": max((r.tier for r in results if r.passed), default=None), "tiers": [r.__dict__ for r in results]}

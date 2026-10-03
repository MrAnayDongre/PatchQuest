"""Per-task results, failure taxonomy, aggregation and comparison."""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

RESULT_SCHEMA = 1


@dataclass
class TaskResult:
    id: str
    category: str
    difficulty: str
    status: str = "failure"  # success | partial | failure
    failure_reason: str | None = None
    run_status: str | None = None
    outcome: str | None = None
    verdict: str | None = None
    oracle_passed: bool = False
    forbidden_change: bool = False
    model_calls: int = 0
    tokens: int = 0
    model_ms: int = 0
    wall_s: float = 0.0
    repair_rounds: int = 0
    degraded_calls: int = 0
    approvals_requested: int = 0
    files_touched: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    error: str | None = None
    run_id: str | None = None
    failure_kind: str | None = None  # the run's typed failure (domain.failures), if it failed
    oracle_returncode: int | None = None
    attribution: str | None = None  # who to blame: see ``attribute``

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify(r: TaskResult) -> tuple[str, str | None]:
    """Return ``(status, failure_reason)`` for a finished task."""
    if r.run_status in ("failed", "cancelled", "interrupted"):
        err = (r.error or "").lower()
        if "budget" in err:
            return "failure", "budget_exceeded"
        if "provider" in err or "llm" in err:
            return "failure", "provider_error"
        if r.run_status == "cancelled":
            return "failure", "timeout"
        return "failure", "run_failed"
    if r.outcome == "no_patch":
        return "failure", "no_patch"
    if r.outcome == "no_changes":
        return "failure", "declined"
    if r.outcome == "conflict":
        return "failure", "conflict"
    if r.outcome == "rejected":
        return "failure", "validation_failed" if r.verdict in ("regression", "unresolved") else "rejected"
    if r.outcome != "applied":
        return "failure", f"unexpected_outcome:{r.outcome}"
    if not r.oracle_passed:
        return "partial", "oracle_failed"  # passed its own checks, failed the hidden ones
    if r.forbidden_change:
        return "partial", "forbidden_change"
    return "success", None


# Who a failed task is attributed to. The point is that a harness bug must never be blamed on the model, and a weak
# model must never be blamed on the harness.
ATTRIBUTIONS = ("HARNESS_FAILURE", "MODEL_FAILURE", "PROVIDER_FAILURE", "TOOL_FAILURE", "SANDBOX_FAILURE",
                "ENVIRONMENT_FAILURE", "ORACLE_FAILURE", "TIMEOUT", "BUDGET")
_BY_KIND = {
    "MODEL_TIMEOUT": "PROVIDER_FAILURE", "MODEL_RATE_LIMIT": "PROVIDER_FAILURE", "MODEL_UNAVAILABLE": "PROVIDER_FAILURE",
    "MODEL_AUTH": "PROVIDER_FAILURE", "MODEL_CONTEXT_OVERFLOW": "MODEL_FAILURE", "MODEL_INVALID_OUTPUT": "MODEL_FAILURE",
    "MODEL_CAPABILITY": "MODEL_FAILURE", "PATCH_PARSE": "MODEL_FAILURE", "PATCH_APPLY": "MODEL_FAILURE",
    "PATCH_CONFLICT": "MODEL_FAILURE", "PATCH_VALIDATION": "MODEL_FAILURE", "TEST_FAILURE": "MODEL_FAILURE",
    "BUDGET_EXHAUSTED": "BUDGET", "USER_CANCELLED": "TIMEOUT", "SANDBOX_FAILURE": "SANDBOX_FAILURE",
    "ENVIRONMENT_FAILURE": "ENVIRONMENT_FAILURE", "TOOL_FAILURE": "TOOL_FAILURE", "TOOL_TIMEOUT": "TOOL_FAILURE",
    "COMMAND_TIMEOUT": "TOOL_FAILURE", "COMMAND_FAILED": "TOOL_FAILURE", "COMMAND_DENIED": "TOOL_FAILURE",
    "CONNECTOR_AUTH": "PROVIDER_FAILURE", "CONNECTOR_UNAVAILABLE": "PROVIDER_FAILURE", "CONNECTOR_RATE_LIMIT": "PROVIDER_FAILURE",
    "DATABASE_FAILURE": "HARNESS_FAILURE", "CHECKPOINT_FAILURE": "HARNESS_FAILURE", "INTERNAL_INVARIANT": "HARNESS_FAILURE",
    "PLUGIN_FAILURE": "HARNESS_FAILURE", "REPLAY_DIVERGED": "HARNESS_FAILURE", "REPOSITORY_DRIFT": "ENVIRONMENT_FAILURE",
}
# Oracle exit codes that mean "the oracle itself could not run" rather than "the hidden tests failed".
_ORACLE_BROKEN = {127, 126, -1, -9, -15}


def attribute(r: TaskResult, *, scripted: bool) -> str | None:
    """Attribute a task that did not succeed. ``scripted`` runs have no model: any failure there is the harness's."""
    if r.status == "success":
        return None
    if r.oracle_returncode in _ORACLE_BROKEN:
        return "ORACLE_FAILURE"
    if scripted:
        return "HARNESS_FAILURE"  # the reference solution is applied by the scripted provider; failing is a harness bug
    if r.failure_kind in _BY_KIND:
        return _BY_KIND[r.failure_kind]
    if r.failure_reason in ("timeout",):
        return "TIMEOUT"
    if r.failure_reason == "budget_exceeded":
        return "BUDGET"
    if r.failure_reason in ("provider_error",):
        return "PROVIDER_FAILURE"
    if r.failure_reason in ("declined", "no_patch", "validation_failed", "rejected", "oracle_failed", "forbidden_change", "conflict"):
        return "MODEL_FAILURE"
    return "HARNESS_FAILURE"  # an outcome nobody planned for is treated as ours to explain


def summarize(results: list[TaskResult]) -> dict[str, Any]:
    def block(rs: list[TaskResult]) -> dict[str, Any]:
        n = len(rs)
        count = lambda s: sum(1 for r in rs if r.status == s)  # noqa: E731
        return {
            "tasks": n, "success": count("success"), "partial": count("partial"), "failure": count("failure"),
            "success_rate": round(count("success") / n, 4) if n else 0.0,
        }

    cats = sorted({r.category for r in results})
    reasons: dict[str, int] = {}
    for r in results:
        if r.failure_reason:
            reasons[r.failure_reason] = reasons.get(r.failure_reason, 0) + 1
    attributions: dict[str, int] = {}
    for r in results:
        if r.attribution:
            attributions[r.attribution] = attributions.get(r.attribution, 0) + 1
    calls = [r.model_calls for r in results]
    return {
        "attribution": dict(sorted(attributions.items(), key=lambda kv: -kv[1])),
        "overall": block(results),
        "by_category": {c: block([r for r in results if r.category == c]) for c in cats},
        "failure_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "totals": {"tokens": sum(r.tokens for r in results), "model_calls": sum(calls),
                   "model_ms": sum(r.model_ms for r in results), "wall_s": round(sum(r.wall_s for r in results), 2)},
        "per_task_medians": {
            "model_calls": statistics.median(calls) if calls else 0,
            "wall_s": round(statistics.median([r.wall_s for r in results]), 2) if results else 0,
            "lines_changed": statistics.median([r.lines_added + r.lines_removed for r in results]) if results else 0,
        },
    }


def compare(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Diff two result files. A *regression* is a task that succeeded before and does not now."""
    if base.get("corpus_digest") != new.get("corpus_digest"):
        raise ValueError("results were produced from different corpora; refusing to compare")
    b = {t["id"]: t for t in base["tasks"]}
    n = {t["id"]: t for t in new["tasks"]}
    regressions, fixes, changed = [], [], []
    for tid in sorted(b.keys() & n.keys()):
        before, after = b[tid]["status"], n[tid]["status"]
        if before == after:
            continue
        changed.append({"id": tid, "from": before, "to": after, "reason": n[tid].get("failure_reason")})
        if before == "success":
            regressions.append(tid)
        elif after == "success":
            fixes.append(tid)
    rate = lambda d: d["summary"]["overall"]["success_rate"]  # noqa: E731
    return {"success_rate": {"base": rate(base), "new": rate(new), "delta": round(rate(new) - rate(base), 4)},
            "regressions": regressions, "fixes": fixes, "changed": changed,
            "tokens": {"base": base["summary"]["totals"]["tokens"], "new": new["summary"]["totals"]["tokens"]}}


@dataclass
class EvalReport:
    schema: int
    environment: dict[str, Any]
    corpus_digest: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"result_schema": self.schema, "environment": self.environment, "corpus_digest": self.corpus_digest,
                "summary": self.summary, "tasks": self.tasks}

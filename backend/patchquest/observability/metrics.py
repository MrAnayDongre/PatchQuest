"""Run metrics computed from what the database already holds: runs, model calls, approvals, events.

Definitions are deliberately plain and stated here, because a metric nobody can define is a metric nobody
can trust:

* **finished**           a run in ``completed``, ``failed`` or ``cancelled``.
* **success**            a *completed* run whose outcome is ``applied``, ``read_only`` or ``no_changes``.
* **validation pass**    among runs that reached a verdict, those with ``passed`` (or ``no_tests``).
* **first-pass success** a success that needed no repair round, no patch retry and no resume.
* **resume success**     among runs that were resumed (attempt > 1), those that completed.
* **cost**               only when ``pricing`` is configured for the model; local engines report compute time.

Percentiles are nearest-rank over the runs in the window (exact, O(n log n)); fine for the volumes a single
install holds. Nothing here invents a number: an empty denominator is reported as ``null``.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

FINISHED = ("completed", "failed", "cancelled")
SUCCESS_OUTCOMES = ("applied", "read_only", "no_changes")
GROUPINGS = {"model": "model", "provider": "provider", "repository": "repo_path", "workspace": "workspace_id"}


@dataclass(frozen=True)
class MetricsQuery:
    workspace_ids: list[str] | None = None  # None: every workspace
    since: str | None = None  # ISO timestamp
    until: str | None = None
    group_by: str | None = None  # one of GROUPINGS
    pricing: dict[str, dict[str, float]] = field(default_factory=dict)  # model -> {input_per_mtok, output_per_mtok}


def parse_window(text: str, now: datetime | None = None) -> str:
    """'24h', '7d', '30m' -> ISO timestamp that long before ``now``."""
    units = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    if len(text) < 2 or text[-1] not in units or not text[:-1].isdigit():
        raise ValueError(f"expected a window like 30m, 24h, 7d or 2w, got '{text}'")
    return ((now or datetime.now(UTC)) - timedelta(**{units[text[-1]]: int(text[:-1])})).isoformat()


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0..100); None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


def _in_clause(column: str, values: list[str]) -> tuple[str, list[str]]:
    return f"{column} IN ({','.join('?' * len(values)) or 'NULL'})", list(values)


def _fetch_runs(conn: sqlite3.Connection, q: MetricsQuery) -> list[dict[str, Any]]:
    where, params = ["1 = 1"], []
    if q.workspace_ids is not None:
        clause, vals = _in_clause("workspace_id", q.workspace_ids)
        where.append(clause)
        params += vals
    if q.since:
        where.append("created_at >= ?")
        params.append(q.since)
    if q.until:
        where.append("created_at < ?")
        params.append(q.until)
    rows = conn.execute(f"SELECT * FROM runs WHERE {' AND '.join(where)}", params).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def _per_run(conn: sqlite3.Connection, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Aggregates keyed by run id, gathered with one query per source (chunked for SQLite's variable limit)."""
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT run_id, COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, "
                f"COALESCE(SUM(duration_ms),0) ms, MIN(started_at) first_call FROM model_calls "
                f"WHERE run_id IN ({marks}) AND status = 'ok' GROUP BY run_id", chunk):
            d = out[r["run_id"]]
            d["model_calls"], d["prompt_tokens"], d["completion_tokens"] = r["calls"], r["pt"], r["ct"]
            d["model_ms"], d["first_call"] = r["ms"], r["first_call"]
        for r in conn.execute(
                f"SELECT run_id, type, COUNT(*) n FROM run_events WHERE run_id IN ({marks}) AND type IN "
                f"('repair_started','patch_retry','retry_scheduled','command_executed','approval_decided','provider_failover') "
                f"GROUP BY run_id, type", chunk):
            out[r["run_id"]][r["type"]] = r["n"]
        for r in conn.execute(
                f"SELECT run_id, created_at, resolved_at FROM approvals WHERE run_id IN ({marks}) "
                f"AND resolved_at IS NOT NULL AND status IN ('approved','denied','cancelled')", chunk):
            wait = _seconds(r["created_at"], r["resolved_at"])
            if wait is not None:
                out[r["run_id"]].setdefault("approval_waits", []).append(wait)
    return out


def _block(runs: list[dict[str, Any]], extra: dict[str, dict[str, Any]], pricing: dict[str, dict[str, float]]) -> dict[str, Any]:
    finished = [r for r in runs if r["status"] in FINISHED]
    successes = [r for r in finished if r["status"] == "completed" and r["outcome"] in SUCCESS_OUTCOMES]
    with_verdict = [r for r in finished if r["verdict"]]
    passed = [r for r in with_verdict if r["verdict"] in ("passed", "no_tests")]
    resumed = [r for r in runs if (r["attempt"] or 1) > 1]
    resumed_ok = [r for r in resumed if r["status"] == "completed"]

    def e(run: dict[str, Any], key: str) -> float:
        return float(extra.get(run["id"], {}).get(key, 0) or 0)

    first_pass = [r for r in successes if (r["attempt"] or 1) == 1 and not e(r, "repair_started") and not e(r, "patch_retry")]
    durations = [d for r in finished if (d := _seconds(r["created_at"], r["completed_at"])) is not None]
    to_first = [d for r in runs if (d := _seconds(r["created_at"], extra.get(r["id"], {}).get("first_call"))) is not None]
    waits = [w for r in runs for w in extra.get(r["id"], {}).get("approval_waits", [])]
    failures: dict[str, int] = defaultdict(int)
    for r in finished:
        if r["failure_kind"]:
            failures[r["failure_kind"]] += 1

    tokens = sum(e(r, "prompt_tokens") + e(r, "completion_tokens") for r in runs)
    cost: float | None = None
    priced = [r for r in runs if r["model"] in pricing]
    if priced and len(priced) == len([r for r in runs if e(r, "model_calls")]):  # only when every model in the window is priced
        cost = round(sum((e(r, "prompt_tokens") * pricing[r["model"]]["input_per_mtok"]
                          + e(r, "completion_tokens") * pricing[r["model"]]["output_per_mtok"]) / 1e6 for r in priced), 6)

    return {
        "runs": len(runs),
        "finished": len(finished),
        "by_status": dict(sorted(_count(runs, "status").items())),
        "by_outcome": dict(sorted(_count(finished, "outcome").items())),
        "task_success_rate": _ratio(len(successes), len(finished)),
        "validation_pass_rate": _ratio(len(passed), len(with_verdict)),
        "first_pass_success_rate": _ratio(len(first_pass), len(finished)),
        "resume_success_rate": _ratio(len(resumed_ok), len(resumed)),
        "runs_resumed": len(resumed),
        "failure_distribution": dict(sorted(failures.items(), key=lambda kv: -kv[1])),
        "mean_model_calls": _mean([e(r, "model_calls") for r in runs]),
        "mean_patch_attempts": _mean([1 + e(r, "patch_retry") for r in runs if e(r, "model_calls")]),
        "mean_repair_rounds": _mean([e(r, "repair_started") for r in finished]),
        "commands_per_run": _mean([e(r, "command_executed") for r in runs]),
        "retry_rate": _ratio(int(sum(e(r, "retry_scheduled") for r in runs)), int(sum(e(r, "model_calls") for r in runs))),
        "provider_failovers": int(sum(e(r, "provider_failover") for r in runs)),
        "human_interventions": int(sum(e(r, "approval_decided") for r in runs)),
        "approval_latency_s": {"p50": percentile(waits, 50), "p95": percentile(waits, 95), "count": len(waits)},
        "time_to_first_model_call_s": {"p50": percentile(to_first, 50), "p95": percentile(to_first, 95)},
        "time_to_completion_s": {"p50": percentile(durations, 50), "p95": percentile(durations, 95)},
        "tokens": {"total": int(tokens), "prompt": int(sum(e(r, "prompt_tokens") for r in runs)),
                   "completion": int(sum(e(r, "completion_tokens") for r in runs)),
                   "per_success": round(tokens / len(successes), 1) if successes else None},
        "model_compute_s": round(sum(e(r, "model_ms") for r in runs) / 1000, 2),
        "cost": {"total": cost, "per_success": round(cost / len(successes), 6) if cost is not None and successes else None,
                 "currency": "USD" if cost is not None else None},
    }


def _count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r[key]:
            counts[r[key]] += 1
    return counts


def model_latency(conn: sqlite3.Connection, q: MetricsQuery) -> list[dict[str, Any]]:
    """Per provider/model call latency and error rate over the window."""
    where, params = ["1 = 1"], []
    if q.workspace_ids is not None:
        clause, vals = _in_clause("r.workspace_id", q.workspace_ids)
        where.append(clause)
        params += vals
    if q.since:
        where.append("m.started_at >= ?")
        params.append(q.since)
    rows = conn.execute("SELECT m.provider, m.model, m.status, m.duration_ms FROM model_calls m "
                        f"JOIN runs r ON r.id = m.run_id WHERE {' AND '.join(where)}", params).fetchall()
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        grouped[(r["provider"] or "", r["model"] or "")].append(r)
    out = []
    for (provider, model), calls in sorted(grouped.items()):
        ok = [c["duration_ms"] for c in calls if c["status"] == "ok" and c["duration_ms"] is not None]
        out.append({"provider": provider, "model": model, "calls": len(calls),
                    "error_rate": _ratio(sum(1 for c in calls if c["status"] != "ok"), len(calls)),
                    "latency_ms": {"p50": percentile(ok, 50), "p95": percentile(ok, 95)}})
    return out


def compute(conn: sqlite3.Connection, q: MetricsQuery) -> dict[str, Any]:
    if q.group_by is not None and q.group_by not in GROUPINGS:
        raise ValueError(f"cannot group by '{q.group_by}' (choose from {', '.join(sorted(GROUPINGS))})")
    runs = _fetch_runs(conn, q)
    extra = _per_run(conn, [r["id"] for r in runs])
    result: dict[str, Any] = {
        "window": {"since": q.since, "until": q.until, "workspaces": q.workspace_ids},
        "totals": _block(runs, extra, q.pricing),
        "models": model_latency(conn, q),
    }
    if q.group_by:
        key = GROUPINGS[q.group_by]
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in runs:
            groups[str(r[key] or "unknown")].append(r)
        result["groups"] = {name: _block(rs, extra, q.pricing) for name, rs in sorted(groups.items())}
    return result

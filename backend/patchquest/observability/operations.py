"""Operational metrics: how the platform around the agents is behaving.

``metrics.compute`` answers "are the agents succeeding?"; this answers "is the machinery healthy?": context quality,
what memory costs and saves, how often policy said no, how often workers died and runs were recovered, how
integrations perform, and (install-wide only) how plugins behave. Everything is derived from the ledger, workflow
history, connector deliveries and plugin events already stored; nothing is invented, an empty denominator is ``null``.

Definitions:
* **context precision**  among runs that applied a patch: selected files that were also patched / files selected.
  (Low is not "wrong" - reading a file without editing it is normal - but a falling trend means noisier context.)
* **context tokens**     characters of selected context / 4, the same rough estimate used for budgeting.
* **memory**             sums of the ``memory_selected`` events: considered, selected, tokens injected, rejected as stale,
                         duplicates avoided, plus how many runs withheld memory under policy.
* **policy denials**     blocked commands whose block came from a policy, plus runs stopped by ``model.use`` policy.
* **recoveries**         runs another worker took over after the first stopped renewing its lease.
* **connector actions**  per workflow action: steps started, steps failed, latency of finished steps.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from patchquest.observability.metrics import MetricsQuery, _in_clause, _ratio, _seconds, percentile


def _run_ids(conn: sqlite3.Connection, q: MetricsQuery) -> list[str]:
    where, params = ["1 = 1"], []
    if q.workspace_ids is not None:
        clause, vals = _in_clause("workspace_id", q.workspace_ids)
        where.append(clause)
        params += vals
    if q.since:
        where.append("created_at >= ?")
        params.append(q.since)
    return [r["id"] for r in conn.execute(f"SELECT id FROM runs WHERE {' AND '.join(where)}", params)]


def _events(conn: sqlite3.Connection, run_ids: list[str], types: tuple[str, ...]) -> list[sqlite3.Row]:
    out: list[sqlite3.Row] = []
    for i in range(0, len(run_ids), 400):
        chunk = run_ids[i:i + 400]
        out += conn.execute(f"SELECT run_id, type, actor, payload_json FROM run_events WHERE run_id IN ({','.join('?' * len(chunk))}) "
                            f"AND type IN ({','.join('?' * len(types))})", [*chunk, *types]).fetchall()
    return out


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        data = json.loads(row["payload_json"]) if row["payload_json"] else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _context(events: list[sqlite3.Row]) -> dict[str, Any]:
    selected: dict[str, set[str]] = defaultdict(set)
    chars: dict[str, int] = defaultdict(int)
    patched: dict[str, set[str]] = defaultdict(set)
    for e in events:
        data = _payload(e)
        if e["type"] == "context_selected":
            for item in data.get("items", []):
                selected[e["run_id"]].add(item.get("path", ""))
                chars[e["run_id"]] += int(item.get("chars") or 0)
        elif e["type"] == "patch_applied":
            patched[e["run_id"]].update(data.get("files", []))
    overlap = sum(len(selected[r] & patched[r]) for r in patched if r in selected)
    considered = sum(len(selected[r]) for r in patched if r in selected)
    return {"runs_with_context": len(selected),
            "mean_files_selected": round(sum(len(v) for v in selected.values()) / len(selected), 2) if selected else None,
            "mean_context_tokens": round(sum(chars.values()) / 4 / len(selected), 1) if selected else None,
            "runs_with_applied_patch": len(patched), "context_precision": _ratio(overlap, considered)}


def _memory(events: list[sqlite3.Row], run_count: int) -> dict[str, Any]:
    totals: dict[str, int] = defaultdict(int)
    runs = 0
    for e in events:
        if e["type"] == "memory_selected":
            runs += 1
            for key in ("considered", "selected", "tokens", "stale_rejected", "low_confidence_rejected", "duplicates_avoided", "not_relevant", "over_budget"):
                totals[key] += int(_payload(e).get(key) or 0)
    return {"runs_using_memory": runs, "items_considered": totals["considered"], "items_selected": totals["selected"],
            "tokens_injected": totals["tokens"], "mean_tokens_per_run": round(totals["tokens"] / runs, 1) if runs else None,
            "stale_rejected": totals["stale_rejected"], "low_confidence_rejected": totals["low_confidence_rejected"],
            "duplicates_avoided": totals["duplicates_avoided"], "not_relevant": totals["not_relevant"],
            "selection_rate": _ratio(totals["selected"], totals["considered"]),
            "runs_withheld_by_policy": sum(1 for e in events if e["type"] == "memory_withheld"),
            "repository_profile_changes": sum(1 for e in events if e["type"] == "repository_profile_changed"),
            "memories_invalidated": sum(len(_payload(e).get("ids", [])) for e in events if e["type"] == "memory_invalidated"),
            "share_of_runs": _ratio(runs, run_count)}


def _policy(events: list[sqlite3.Row]) -> dict[str, Any]:
    commands = sum(1 for e in events if e["type"] == "command_blocked" and _payload(e).get("policy"))
    models = sum(1 for e in events if e["type"] == "model_denied")
    return {"commands_denied": commands, "model_use_denied": models,
            "commands_blocked_by_command_gate": sum(1 for e in events if e["type"] == "command_blocked" and not _payload(e).get("policy")),
            "approvals_requested": sum(1 for e in events if e["type"] == "approval_requested")}


def _workers(events: list[sqlite3.Row], include_queue: bool) -> dict[str, Any]:
    from patchquest.runtime import queue

    recoveries = sum(1 for e in events if e["type"] == "run_interrupted" and e["actor"] == "recovery")
    return {"recoveries": recoveries, "queue_now": queue.stats() if include_queue else None}  # the queue is shared by every tenant


def _workflows(conn: sqlite3.Connection, q: MetricsQuery) -> dict[str, Any]:
    where, params = ["1 = 1"], []
    if q.workspace_ids is not None:
        clause, vals = _in_clause("r.workspace_id", q.workspace_ids)
        where.append(clause)
        params += vals
    if q.since:
        where.append("r.created_at >= ?")
        params.append(q.since)
    cond = " AND ".join(where)
    names = {(r["workflow_run_id"], r["node_id"]): r["message"] for r in conn.execute(
        "SELECT e.workflow_run_id, e.node_id, e.message FROM workflow_events e JOIN workflow_runs r ON r.id = e.workflow_run_id "
        f"WHERE e.type = 'step_started' AND e.message IS NOT NULL AND {cond}", params)}
    actions: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"latency": [], "ok": [], "failed": []})
    for s in conn.execute("SELECT s.workflow_run_id, s.node_id, s.status, s.started_at, s.finished_at FROM workflow_steps s "
                          f"JOIN workflow_runs r ON r.id = s.workflow_run_id WHERE {cond}", params):
        name = names.get((s["workflow_run_id"], s["node_id"]))
        if not name or s["status"] not in ("succeeded", "failed", "uncertain"):
            continue
        bucket = actions[name]
        bucket["failed" if s["status"] != "succeeded" else "ok"].append(1)
        if s["status"] == "succeeded" and (d := _seconds(s["started_at"], s["finished_at"])) is not None:
            bucket["latency"].append(d)
    per_action = {name: {"steps": len(b["ok"]) + len(b["failed"]), "failed": len(b["failed"]),
                         "failure_rate": _ratio(len(b["failed"]), len(b["ok"]) + len(b["failed"])),
                         "latency_s": {"p50": percentile(b["latency"], 50), "p95": percentile(b["latency"], 95)}}
                  for name, b in sorted(actions.items())}
    inbound_where, inbound_params = ["1 = 1"], []
    if q.workspace_ids is not None:
        clause, vals = _in_clause("workspace_id", q.workspace_ids)
        inbound_where.append(clause)
        inbound_params += vals
    if q.since:
        inbound_where.append("received_at >= ?")
        inbound_params.append(q.since)
    inbound: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in conn.execute(f"SELECT source, status, COUNT(*) n FROM connector_events WHERE {' AND '.join(inbound_where)} GROUP BY source, status", inbound_params):
        inbound[r["source"]][r["status"].lower()] += r["n"]
    return {"actions": per_action, "inbound": {k: dict(v) for k, v in sorted(inbound.items())}}


def _plugins(conn: sqlite3.Connection, q: MetricsQuery) -> dict[str, Any]:
    rows = conn.execute("SELECT plugin, type, outcome, duration_ms FROM plugin_events WHERE ts >= ?", (q.since or "",)).fetchall()
    out: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        grouped[r["plugin"]].append(r)
    for name, items in sorted(grouped.items()):
        calls = [r for r in items if r["type"] in ("invoked", "invoke_failed")]
        ok = [r["duration_ms"] for r in items if r["type"] == "invoked" and r["duration_ms"] is not None]
        out[name] = {"calls": len(calls), "failed": sum(1 for r in calls if r["type"] == "invoke_failed"),
                     "failure_rate": _ratio(sum(1 for r in calls if r["type"] == "invoke_failed"), len(calls)),
                     "denied": sum(1 for r in items if r["type"] in ("denied", "needs_approval")),
                     "quarantines": sum(1 for r in items if r["type"] == "quarantined"),
                     "latency_ms": {"p50": percentile(ok, 50), "p95": percentile(ok, 95)}}
    return out


def compute(conn: sqlite3.Connection, q: MetricsQuery, *, include_install_wide: bool = False) -> dict[str, Any]:
    """The operations report. ``include_install_wide`` adds data that belongs to the whole installation rather than a
    tenant (plugins, the global queue): only for callers who administer the organisation or run the CLI."""
    run_ids = _run_ids(conn, q)
    types = ("context_selected", "patch_applied", "memory_selected", "memory_withheld", "repository_profile_changed", "memory_invalidated",
             "command_blocked", "model_denied", "approval_requested", "run_interrupted")
    events = _events(conn, run_ids, types)
    result: dict[str, Any] = {
        "window": {"since": q.since, "workspaces": q.workspace_ids},
        "runs": len(run_ids),
        "context": _context(events),
        "memory": _memory(events, len(run_ids)),
        "policy": _policy(events),
        "workers": _workers(events, include_install_wide),
        "workflows": _workflows(conn, q),
    }
    if include_install_wide:
        result["plugins"] = _plugins(conn, q)
    return result

"""Export a run as an OpenTelemetry trace (OTLP/JSON), built purely from the durable record.

The ledger already knows when every phase, model call and command started and ended, so the trace is a
*view* of it, not a second instrumentation path that can drift. Ids are deterministic (hashes of the run id
and event ids), so exporting twice gives the same trace and a re-export updates rather than duplicates it.
No OpenTelemetry package is needed; the output is plain JSON a collector accepts at ``/v1/traces``.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from typing import Any

from patchquest.persistence import ledger

SPAN_KIND_INTERNAL = 1
STATUS_OK, STATUS_ERROR = 1, 2


def _hex(seed: str, nbytes: int) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[: nbytes * 2]


def _ns(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)
    except ValueError:
        return None


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        wrapped: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}
    elif isinstance(value, float):
        wrapped = {"doubleValue": value}
    else:
        wrapped = {"stringValue": str(value)}
    return {"key": key, "value": wrapped}


def _span(trace_id: str, seed: str, name: str, start: int, end: int, parent: str | None, attrs: dict[str, Any],
          error: str | None = None) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": trace_id, "spanId": _hex(seed, 8), "name": name, "kind": SPAN_KIND_INTERNAL,
        "startTimeUnixNano": str(start), "endTimeUnixNano": str(max(end, start)),
        "attributes": [_attr(k, v) for k, v in attrs.items() if v is not None],
        "status": {"code": STATUS_ERROR, "message": error} if error else {"code": STATUS_OK},
    }
    if parent:
        span["parentSpanId"] = parent
    return span


def build_trace(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise LookupError(run_id)
    events = ledger.read(conn, run_id, limit=1_000_000)
    trace_id = _hex(f"run:{run_id}", 16)
    start = _ns(run["created_at"]) or 0
    last = max((t for e in events if (t := _ns(e["created_at"]))), default=start)
    end = _ns(run["completed_at"]) or last
    root_id = _hex(f"root:{run_id}", 8)

    spans = [_span(trace_id, f"root:{run_id}", "patchquest.run", start, end, None, {
        "patchquest.run_id": run_id, "patchquest.workspace_id": run["workspace_id"], "patchquest.status": run["status"],
        "patchquest.outcome": run["outcome"], "patchquest.verdict": run["verdict"], "patchquest.attempt": run["attempt"],
        "patchquest.provider": run["provider"], "patchquest.model": run["model"],
        "patchquest.failure_kind": run["failure_kind"], "patchquest.parent_run_id": run["parent_run_id"],
    }, error=run["failure_kind"] if run["status"] in ("failed", "cancelled") else None)]

    open_phase: dict[str, tuple[int, int, int]] = {}  # phase -> (start ns, attempt, id of its phase_started event)
    phase_span: dict[str, str] = {}  # phase -> span id of its latest span
    open_cmd: tuple[int, str, dict[str, Any]] | None = None
    call_phase: dict[Any, str] = {}
    current = ""  # the phase in progress: commands and approvals do not record one, so ledger order decides
    for e in events:
        t = _ns(e["created_at"]) or start
        kind, phase = e["type"], e["phase"]
        if kind == "phase_started" and phase:
            current = phase
            open_phase[phase] = (t, e["attempt"], e["id"])
            phase_span[phase] = _hex(f"phase:{run_id}:{phase}:{e['id']}", 8)
        elif kind in ("phase_completed", "phase_skipped", "phase_failed", "phase_blocked") and phase in open_phase:
            began, attempt, started_id = open_phase.pop(phase)
            span = _span(trace_id, f"phase:{run_id}:{phase}:{started_id}", f"phase {phase}", began, t,
                         root_id, {"patchquest.phase": phase, "patchquest.attempt": attempt, "patchquest.result": kind.removeprefix("phase_")},
                         error=e["message"] if kind == "phase_failed" else None)
            spans.append(span)
        elif kind == "model_call":
            call_phase[(e["payload"] or {}).get("call_id")] = phase or ""
        elif kind == "command_started":
            open_cmd = (t, phase or current, e["payload"] or {})
        elif kind in ("command_executed", "command_denied", "command_blocked") and open_cmd is not None:
            began, ph, payload = open_cmd
            open_cmd = None
            spans.append(_span(trace_id, f"cmd:{run_id}:{e['id']}", "command", began, t, phase_span.get(ph, root_id), {
                "patchquest.command": payload.get("command"), "patchquest.side_effect": payload.get("side_effect"),
                "patchquest.returncode": (e["payload"] or {}).get("returncode"), "patchquest.result": kind.removeprefix("command_")},
                error=None if kind == "command_executed" else kind.removeprefix("command_")))
    for phase, (began, attempt, started_id) in open_phase.items():  # still open: the process died or the run was cancelled mid-phase
        spans.append(_span(trace_id, f"phase:{run_id}:{phase}:{started_id}", f"phase {phase}", began, last,
                           root_id, {"patchquest.phase": phase, "patchquest.attempt": attempt, "patchquest.result": "unfinished"},
                           error="did not finish"))

    for call in conn.execute("SELECT id, role, provider, model, started_at, duration_ms, prompt_tokens, completion_tokens, status, "
                             "attempts FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,)):
        began = _ns(call["started_at"]) or start
        parent = phase_span.get(call_phase.get(call["id"], ""), root_id)
        spans.append(_span(trace_id, f"model:{run_id}:{call['id']}", f"model {call['role']}", began,
                           began + int((call["duration_ms"] or 0) * 1_000_000), parent, {
                               "gen_ai.system": call["provider"], "gen_ai.request.model": call["model"],
                               "gen_ai.usage.input_tokens": call["prompt_tokens"], "gen_ai.usage.output_tokens": call["completion_tokens"],
                               "patchquest.role": call["role"], "patchquest.attempts": call["attempts"]},
                           error=None if call["status"] == "ok" else call["status"]))

    resource = {"attributes": [_attr("service.name", "patchquest"), _attr("patchquest.run_id", run_id)]}
    return {"resourceSpans": [{"resource": resource,
                               "scopeSpans": [{"scope": {"name": "patchquest.ledger", "version": "1"}, "spans": spans}]}]}

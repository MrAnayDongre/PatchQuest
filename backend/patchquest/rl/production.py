"""Turn a real PatchQuest run into a training/evaluation trajectory.

The gym (``env.py``) simulates episodes; this exports what actually happened in production, from the durable record
alone (ledger, model calls, approvals, final verdict), so the loop "run -> review -> train" needs no extra
instrumentation. It uses a separate schema (``kind: production_trajectory``) because the two differ in an important way:
a production run has no hidden oracle, so its reward is built from what was observed and from what people decided.

Reward (documented so it can be challenged; each component is reported on its own):

* ``validation``  +1.0 when the run completed with verdict ``passed`` (or ``no_tests`` with a read-only task) and its
                  outcome was ``applied`` / ``read_only``; 0 otherwise. This is the only "did it work" signal that exists
                  without an oracle: the repository's own tests, in a shadow workspace, before anything was promoted.
* ``human``       -0.25 for each approval a person denied or modified (they disagreed with the agent), 0 for approvals
                  granted. Humans are the only available label for patch quality beyond the tests.
* ``safety``      -0.5 for each command or model use a policy or the command gate blocked, and -0.5 if a secret finding
                  stopped a patch.
* ``cost``        -0.01 per model call: a small, constant preference for fewer calls.

``total`` is their sum. These are defaults for ranking and filtering runs, not a claim about the right objective.
Prompts and responses appear only when the run recorded them (``agent.record_model_io``) and the caller asks for them.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from patchquest.persistence import ledger
from patchquest.tools.secret_guard import redact_secrets

SCHEMA_VERSION = 1
STEP_EVENTS = {"plan_created": "plan", "context_selected": "context", "memory_selected": "memory", "patch_proposed": "patch_proposed",
               "patch_staged": "patch_staged", "command_executed": "command", "command_blocked": "command_blocked",
               "command_denied": "command_denied", "tests_completed": "validation", "repair_started": "repair",
               "approval_decided": "approval", "patch_applied": "patch_applied", "patch_rejected": "patch_rejected",
               "decision_explained": "decision", "model_denied": "policy_denied", "secret_findings": "secret_findings"}


def _clip(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str):
        return redact_secrets(value[:limit])
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:100]]
    return value


def build(conn: sqlite3.Connection, run_id: str, *, include_model_io: bool = False) -> dict[str, Any]:
    """The trajectory of ``run_id``. Raises ``LookupError`` for an unknown run."""
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise LookupError(run_id)
    events = ledger.read(conn, run_id, limit=100_000)
    calls = conn.execute("SELECT * FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    approvals = conn.execute("SELECT type, status, decision, side_effect, risk FROM approvals WHERE run_id = ?", (run_id,)).fetchall()

    steps: list[dict[str, Any]] = []
    for c in calls:
        step: dict[str, Any] = {"ts": c["started_at"], "type": "model_call", "role": c["role"],
                                "action": {"provider": c["provider"], "model": c["model"]},
                                "result": {"status": c["status"], "attempts": c["attempts"], "duration_ms": c["duration_ms"],
                                           "prompt_tokens": c["prompt_tokens"], "completion_tokens": c["completion_tokens"],
                                           "degraded": c["degraded"], "error": (c["error"] or "")[:200] or None}}
        if include_model_io:
            step["observation"] = {"request": _clip(c["request_json"], 20000)}
            step["action"]["response"] = _clip(c["response_text"], 20000)
        steps.append(step)
    for e in events:
        kind = STEP_EVENTS.get(e["type"])
        if kind is not None:
            steps.append({"ts": e["created_at"], "type": kind, "phase": e["phase"], "action": _clip(e["payload"] or {}),
                          "result": {"message": _clip(e["message"] or "")}})
    steps.sort(key=lambda s: str(s["ts"]))
    for index, step in enumerate(steps):
        step["index"] = index

    model_calls = len(calls)
    blocked = sum(1 for s in steps if s["type"] in ("command_blocked", "policy_denied") and (s["action"].get("policy") or s["type"] == "policy_denied"))
    blocked += sum(1 for s in steps if s["type"] == "command_blocked" and not s["action"].get("policy"))
    overruled = sum(1 for a in approvals if a["status"] in ("denied", "cancelled") or a["decision"] in ("DENY", "MODIFY"))
    worked = (run["status"] == "completed" and run["verdict"] in ("passed", "no_tests") and run["outcome"] in ("applied", "read_only"))
    components = {"validation": 1.0 if worked else 0.0, "human": -0.25 * overruled,
                  "safety": -0.5 * blocked - (0.5 if any(s["type"] == "secret_findings" for s in steps) else 0.0),
                  "cost": -0.01 * model_calls}
    return {
        "kind": "production_trajectory", "schema_version": SCHEMA_VERSION,
        "run": {"id": run_id, "status": run["status"], "outcome": run["outcome"], "verdict": run["verdict"], "failure_kind": run["failure_kind"],
                "provider": run["provider"], "model": run["model"], "attempt": run["attempt"], "lineage_kind": run["lineage_kind"],
                "parent_run_id": run["parent_run_id"], "created_at": run["created_at"], "completed_at": run["completed_at"],
                "task": _clip(run["task"], 1000)},
        "includes_model_io": include_model_io,
        "reward": {**components, "total": round(sum(components.values()), 4)},
        "totals": {"model_calls": model_calls, "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in calls),
                   "completion_tokens": sum(c["completion_tokens"] or 0 for c in calls), "steps": len(steps), "approvals": len(approvals),
                   "approvals_overruled": overruled},
        "steps": steps,
    }


def to_jsonl(trajectory: dict[str, Any]) -> str:
    """Header line, then one line per step (the same layout as the gym's files, so tools can read both)."""
    header = {k: v for k, v in trajectory.items() if k != "steps"} | {"kind": "header", "trajectory_kind": trajectory["kind"]}
    return "\n".join([json.dumps(header, default=str), *(json.dumps({"kind": "step", **s}, default=str) for s in trajectory["steps"])]) + "\n"

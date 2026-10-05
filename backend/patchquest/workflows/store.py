"""Workflow persistence: versioned definitions, runs, steps and the append-only workflow event log."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from patchquest.domain.workflows import Workflow, parse, to_dict
from patchquest.persistence.ledger import now_iso


class WorkflowNotFound(LookupError):
    pass


def save_version(conn: sqlite3.Connection, workspace_id: str, wf: Workflow, created_by: str | None) -> tuple[str, int]:
    """Store ``wf`` as the next version of its name. Editing never mutates history: runs stay on their version."""
    last = conn.execute("SELECT COALESCE(MAX(version), 0) FROM workflows WHERE workspace_id = ? AND name = ?",
                        (workspace_id, wf.name)).fetchone()[0]
    workflow_id = f"wf_{uuid.uuid4().hex[:16]}"
    conn.execute("INSERT INTO workflows (id, workspace_id, name, version, definition_json, trigger_type, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (workflow_id, workspace_id, wf.name, last + 1, json.dumps(to_dict(wf), sort_keys=True), wf.trigger.type,
                  created_by, now_iso()))
    return workflow_id, last + 1


def load(conn: sqlite3.Connection, workflow_id: str) -> tuple[Workflow, dict[str, Any]]:
    row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
    if row is None:
        raise WorkflowNotFound(workflow_id)
    return parse(json.loads(row["definition_json"])), {k: row[k] for k in row.keys() if k != "definition_json"}


def latest_active(conn: sqlite3.Connection, workspace_id: str, name: str) -> str | None:
    row = conn.execute("SELECT id FROM workflows WHERE workspace_id = ? AND name = ? AND status = 'active' "
                       "ORDER BY version DESC LIMIT 1", (workspace_id, name)).fetchone()
    return row["id"] if row else None


def active_for_trigger(conn: sqlite3.Connection, trigger_type: str, workspace_id: str | None = None) -> list[str]:
    """Latest active version of each workflow (per workspace) listening for ``trigger_type``."""
    sql = ("SELECT w.id FROM workflows w WHERE w.trigger_type = ? AND w.status = 'active' AND w.version = "
           "(SELECT MAX(version) FROM workflows x WHERE x.workspace_id = w.workspace_id AND x.name = w.name AND x.status = 'active')")
    params: list[Any] = [trigger_type]
    if workspace_id is not None:
        sql += " AND w.workspace_id = ?"
        params.append(workspace_id)
    return [r["id"] for r in conn.execute(sql, params)]


def event(conn: sqlite3.Connection, run_id: str, type_: str, *, node_id: str | None = None, actor: str = "engine",
          message: str | None = None, payload: dict[str, Any] | None = None) -> None:
    conn.execute("INSERT INTO workflow_events (workflow_run_id, type, node_id, actor, message, payload_json, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (run_id, type_, node_id, actor, message, json.dumps(payload, default=str) if payload else None, now_iso()))


def events(conn: sqlite3.Connection, run_id: str, after: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM workflow_events WHERE workflow_run_id = ? AND id > ? ORDER BY id", (run_id, after)).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["payload"] = json.loads(d.pop("payload_json")) if d.get("payload_json") else None
        out.append(d)
    return out


def steps(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM workflow_steps WHERE workflow_run_id = ? ORDER BY id", (run_id,)).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["output"] = json.loads(d.pop("output_json")) if d.get("output_json") else None
        out.append(d)
    return out


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise WorkflowNotFound(run_id)
    d = {k: row[k] for k in row.keys()}
    d["trigger"] = json.loads(d.pop("trigger_json"))
    d["vars"] = json.loads(d.pop("vars_json"))
    return d

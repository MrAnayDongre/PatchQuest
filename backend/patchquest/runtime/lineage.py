"""Run lineage: forks and replays are new runs that point at the run they came from.

Nothing is ever copied *into* a parent or rewritten in it. A child records ``parent_run_id`` and the
checkpoint it started from, begins with its own copy of that checkpoint (re-signed for the child, so it
can itself be resumed from the fork point), and writes only its own future events.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from patchquest.config import validate_overrides
from patchquest.database import now_iso
from patchquest.persistence import checkpoints, ledger

MAX_LINEAGE_DEPTH = 50  # a cycle is impossible by construction; this only bounds a pathological chain


def create_child(
    conn: sqlite3.Connection, parent_id: str, *, kind: str, parent_cp: checkpoints.Checkpoint | None,
    provider: str | None = None, model: str | None = None, base_url: str | None = None,
    overrides: dict[str, Any] | None = None, replay_mode: str | None = None, run_id: str | None = None,
    actor: str = "user",
) -> str:
    """Insert a child run (status ``created``) of ``parent_id`` and its first events. Returns its id."""
    parent = conn.execute("SELECT * FROM runs WHERE id = ?", (parent_id,)).fetchone()
    if parent is None:
        raise LookupError(parent_id)
    merged = {**(json.loads(parent["overrides_json"]) if parent["overrides_json"] else {}), **(overrides or {})}
    validate_overrides(merged)
    child_id, now = run_id or str(uuid.uuid4()), now_iso()
    conn.execute(
        """INSERT INTO runs (id, repo_path, task, status, provider, model, model_profile, memory_mode, runtime_mode,
               allow_network, dry_run, base_url, created_at, updated_at, parent_run_id, parent_checkpoint_seq,
               lineage_kind, replay_mode, overrides_json)
           VALUES (?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (child_id, parent["repo_path"], parent["task"], provider or parent["provider"], model or parent["model"],
         parent["model_profile"], parent["memory_mode"], parent["runtime_mode"], parent["allow_network"], parent["dry_run"],
         base_url if base_url is not None else parent["base_url"], now, now, parent_id,
         parent_cp.seq if parent_cp else None, kind, replay_mode, json.dumps(merged) if merged else None))
    ledger.append(conn, child_id, "run_created", message=f"{kind.capitalize()} of run {parent_id[:8]}", actor=actor)
    ledger.append(conn, child_id, f"{kind}_created", actor=actor, message=f"Started from run {parent_id}",
                  payload={"parent_run_id": parent_id, "parent_checkpoint_seq": parent_cp.seq if parent_cp else None,
                           "provider": provider, "model": model, "overrides": overrides or {}, "replay_mode": replay_mode})
    if parent_cp is not None:
        # The child owns a copy of the fork point, so a crash before its first checkpoint resumes from it.
        cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM run_events WHERE run_id = ?", (child_id,)).fetchone()[0]
        checkpoints.save(conn, run_id=child_id, phase=parent_cp.phase, state=parent_cp.state,
                         fingerprint=parent_cp.fingerprint, event_cursor=cursor, attempt=1,
                         runtime_version=parent_cp.runtime_version)
    return child_id


def lineage(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """The run and its ancestors, oldest first."""
    chain: list[dict[str, Any]] = []
    current: str | None = run_id
    while current and len(chain) < MAX_LINEAGE_DEPTH:
        row = conn.execute("SELECT id, status, outcome, verdict, provider, model, parent_run_id, parent_checkpoint_seq, "
                           "lineage_kind, replay_mode, overrides_json, created_at FROM runs WHERE id = ?", (current,)).fetchone()
        if row is None:
            break
        chain.append({k: row[k] for k in row.keys()})
        current = row["parent_run_id"]
    return chain[::-1]


def children(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, status, outcome, verdict, provider, model, lineage_kind, replay_mode, "
                        "parent_checkpoint_seq, created_at FROM runs WHERE parent_run_id = ? ORDER BY created_at",
                        (run_id,)).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]

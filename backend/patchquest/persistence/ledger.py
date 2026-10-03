"""The event ledger: append-only, versioned, attributable history of every run.

Rows are never updated or deleted (database triggers enforce it). The one sanctioned change is
``redact``, which blanks an event's payload and message and flags it, so sensitive content can be
removed without breaking the history's order or counts. ``id`` is the global, monotonic cursor
that streaming clients resume from.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

EVENT_SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def append(
    conn: sqlite3.Connection,
    run_id: str,
    event_type: str,
    *,
    phase: str | None = None,
    status: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = "runtime",
    attempt: int = 1,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> tuple[int, str]:
    """Append one event. Returns ``(cursor, event_uid)``."""
    event_uid = uuid.uuid4().hex
    cursor = conn.execute(
        """INSERT INTO run_events (run_id, type, phase, status, message, payload_json, created_at,
               event_uid, schema_version, actor, attempt, correlation_id, causation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, event_type, phase, status, message, json.dumps(payload, default=str) if payload else None,
         now_iso(), event_uid, EVENT_SCHEMA_VERSION, actor, attempt, correlation_id, causation_id),
    )
    if cursor.lastrowid is None:  # INSERT always sets it; fail loudly rather than return a bogus cursor
        raise RuntimeError("event append produced no row id")
    return cursor.lastrowid, event_uid


def redact(conn: sqlite3.Connection, event_id: int) -> None:
    """Blank an event's content in place; the trigger permits nothing else."""
    conn.execute("UPDATE run_events SET payload_json = NULL, message = NULL, redacted = 1 WHERE id = ?", (event_id,))


def read(conn: sqlite3.Connection, run_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                        (run_id, after, limit)).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["payload"] = json.loads(d.pop("payload_json")) if d.get("payload_json") else None
        out.append(d)
    return out

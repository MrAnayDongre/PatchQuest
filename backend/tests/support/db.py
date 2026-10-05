"""Run and event rows."""

from __future__ import annotations

import json
from typing import Any

from patchquest.database import get_db, now_iso


def insert_run(run_id: str, task: str = "task", repo_path: str | None = None, status: str = "created",
               provider: str = "mock", model: str | None = None) -> None:
    """Insert a stub run so foreign keys and lookups work. Idempotent."""
    from tests.support.repos import TEST_REPO

    now = now_iso()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO runs (id, repo_path, task, status, provider, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, repo_path or TEST_REPO, task, status, provider, model, now, now),
        )


def fetch_events(run_id: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, phase, status, message, payload_json FROM run_events WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
    return [
        {"type": r["type"], "phase": r["phase"], "status": r["status"], "message": r["message"],
         "payload": json.loads(r["payload_json"]) if r["payload_json"] else None}
        for r in rows
    ]


def event_types(run_id: str) -> list[str]:
    return [e["type"] for e in fetch_events(run_id)]


def run_row(run_id: str):
    with get_db() as conn:
        return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

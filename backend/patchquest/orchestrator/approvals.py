"""Approval request management."""

from __future__ import annotations

import uuid

from patchquest.database import get_db, now_iso


def create_approval(run_id: str, approval_type: str, command: str | None = None,
                    reason: str | None = None) -> str:
    approval_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            """INSERT INTO approvals (id, run_id, type, command, reason, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, run_id, approval_type, command, reason, "pending", now_iso()),
        )
    return approval_id


def get_pending_approvals(run_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)
        ).fetchall()
    return [dict(r) for r in rows]

"""Startup recovery: reconcile state left behind by a crashed or killed process.

The state machine is in-memory, so after a restart no run marked ``running`` is actually
running. Leaving them as-is shows phantom work forever and keeps approvals pending that
nothing can answer. This marks them ``interrupted`` (recorded as an event, with the phase
they reached) so the history is truthful; resumption builds on this record.
"""

from __future__ import annotations

import logging

from patchquest.database import get_db, insert_event, now_iso

logger = logging.getLogger(__name__)


def recover_interrupted_runs() -> int:
    """Mark in-flight runs interrupted, expire orphaned approvals. Returns runs recovered."""
    with get_db() as conn:
        rows = conn.execute("SELECT id, current_phase FROM runs WHERE status IN ('created', 'running')").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE runs SET status = 'interrupted', outcome = COALESCE(outcome, 'interrupted'), "
                "completed_at = ?, updated_at = ? WHERE id = ?",
                (now_iso(), now_iso(), row["id"]),
            )
            insert_event(conn, row["id"], "run_interrupted", row["current_phase"], "interrupted",
                         "Process stopped while this run was in progress", None)
            conn.execute(
                "UPDATE approvals SET status = 'expired', resolved_at = ? WHERE run_id = ? AND status = 'pending'",
                (now_iso(), row["id"]),
            )
        try:  # scheduled tasks that were mid-run when the process died
            conn.execute("UPDATE scheduled_tasks SET status = 'active' WHERE status = 'running'")
        except Exception:  # noqa: BLE001 - table may not exist on very old databases
            pass
    if rows:
        logger.warning("Recovered %d interrupted run(s)", len(rows))
    return len(rows)

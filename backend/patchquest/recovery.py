"""Startup recovery: reconcile state left behind by a crashed or killed process.

The state machine is in-memory, so after a restart no run marked active is actually running.
Leaving them as-is shows phantom work forever and keeps approvals pending that nothing can answer.
Active runs become ``interrupted`` (resumable from their last checkpoint); a run whose cancel had
already been requested becomes ``cancelled``, honouring the user's intent.
"""

from __future__ import annotations

import logging

from patchquest.database import get_db, now_iso
from patchquest.domain.runs import IllegalTransition, RunStatus
from patchquest.persistence import ledger
from patchquest.persistence.runs import transition

logger = logging.getLogger(__name__)

_ACTIVE = (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL, RunStatus.CANCEL_REQUESTED)


def recover_interrupted_runs() -> int:
    """Settle runs that were in flight when the process died. Returns how many were recovered."""
    recovered = 0
    with get_db() as conn:
        marks = ",".join("?" * len(_ACTIVE))
        rows = conn.execute(
            f"SELECT id, status, attempt, current_phase FROM runs WHERE status IN ({marks}) "
            # Databases from before typed statuses never recorded 'running': a created run with a
            # phase was in flight.
            "OR (status = 'created' AND current_phase IS NOT NULL)", [s.value for s in _ACTIVE]).fetchall()
        # A run that carries a lease belongs to the worker pool: workers recover it when the lease expires.
        rows = [r for r in rows if not conn.execute("SELECT lease_owner FROM runs WHERE id = ?", (r["id"],)).fetchone()[0]]
        for row in rows:
            run_id, status = row["id"], RunStatus(row["status"])
            try:
                if status is RunStatus.CREATED:
                    transition(conn, run_id, RunStatus.RUNNING, actor="recovery", reason="adopted legacy in-flight run",
                               attempt=row["attempt"])
                target, reason = ((RunStatus.CANCELLED, "cancel was requested before the process stopped")
                                  if status is RunStatus.CANCEL_REQUESTED
                                  else (RunStatus.INTERRUPTED, "process stopped while this run was in progress"))
                transition(conn, run_id, target, actor="recovery", reason=reason, attempt=row["attempt"],
                           fields={"outcome": target.value})
                if target is RunStatus.INTERRUPTED:  # the event streaming clients treat as "this run ended"
                    ledger.append(conn, run_id, "run_interrupted", phase=row["current_phase"], status="interrupted",
                                  message=reason, actor="recovery", attempt=row["attempt"])
            except IllegalTransition:
                logger.warning("run %s could not be recovered from %s", run_id, status, exc_info=True)
                continue
            conn.execute("UPDATE approvals SET status = 'expired', resolved_at = ? WHERE run_id = ? AND status = 'pending'",
                         (now_iso(), run_id))
            recovered += 1
        try:  # scheduled tasks that were mid-run when the process died
            conn.execute("UPDATE scheduled_tasks SET status = 'active' WHERE status = 'running'")
        except Exception:
            logger.debug("scheduled_tasks reset skipped", exc_info=True)
    if recovered:
        logger.warning("Recovered %d interrupted run(s)", recovered)
    return recovered

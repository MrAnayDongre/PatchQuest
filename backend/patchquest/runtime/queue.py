"""A durable run queue with worker leases, on the primary database.

* ``enqueue``   run becomes ``queued``.
* ``claim``     atomically give the oldest queued run to a worker (``BEGIN IMMEDIATE``: one writer at a time, so
                two workers can never receive the same run) with a lease that expires unless renewed. If nothing is
                queued, a run whose lease has *expired* (its worker died) is recovered: it is marked
                ``interrupted`` so the normal resume rules decide whether it may continue.
* ``heartbeat`` renew a lease; compare-and-set on owner and epoch, so a worker that lost its lease finds out.
* ``release``   give a finished run's lease back.

Every claim bumps ``lease_epoch``. A worker that stalled past its lease and was replaced can still be running
code: it learns this at its next heartbeat and abandons the run (``RunStateMachine.abandon``). Until then
two executions can overlap for at most one heartbeat interval; fencing every write by epoch would close that
window and is not implemented. For PostgreSQL the claim would use ``FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from patchquest.database import get_db, is_postgres, lock_rows
from patchquest.domain.runs import RunStatus
from patchquest.persistence import ledger
from patchquest.persistence.runs import transition

LIVE = (RunStatus.RUNNING.value, RunStatus.WAITING_APPROVAL.value, RunStatus.CANCEL_REQUESTED.value)


class LeaseLost(RuntimeError):
    """This worker no longer owns the run (its lease expired and the run was given to another worker)."""


@dataclass(frozen=True)
class Claim:
    run_id: str
    kind: str  # 'start': this worker owns the run now. 'recover': a dead worker's run was interrupted; decide on resume.
    epoch: int
    attempt: int


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def enqueue(conn: sqlite3.Connection, run_id: str, *, actor: str, reason: str = "queued for a worker") -> None:
    transition(conn, run_id, RunStatus.QUEUED, actor=actor, reason=reason)
    conn.execute("UPDATE runs SET queued_at = ?, lease_owner = NULL, lease_expires_at = NULL WHERE id = ?", (ledger.now_iso(), run_id))


def claim(worker_id: str, lease_s: float, *, now: datetime | None = None) -> Claim | None:
    moment = now or datetime.fromisoformat(ledger.now_iso())
    with get_db() as conn:
        if not is_postgres():
            conn.execute("BEGIN IMMEDIATE")  # SQLite: one writer at a time serialises claimers across processes
        # PostgreSQL: concurrent claimers each lock a different queued row; SKIP LOCKED passes over rows another holds.
        row = conn.execute("SELECT id, attempt, lease_epoch FROM runs WHERE status = 'queued' ORDER BY queued_at, id LIMIT 1"
                           + lock_rows()).fetchone()
        if row is not None:
            epoch = row["lease_epoch"] + 1
            transition(conn, row["id"], RunStatus.RUNNING, actor=f"worker:{worker_id}", reason="claimed by a worker",
                       attempt=row["attempt"])
            conn.execute("UPDATE runs SET lease_owner = ?, lease_expires_at = ?, lease_epoch = ? WHERE id = ?",
                         (worker_id, _iso(moment + timedelta(seconds=lease_s)), epoch, row["id"]))
            return Claim(row["id"], "start", epoch, row["attempt"])
        dead = conn.execute(
            f"SELECT id, status, attempt, current_phase, lease_owner FROM runs WHERE status IN ({','.join('?' * len(LIVE))}) "
            "AND lease_owner IS NOT NULL AND lease_expires_at < ? ORDER BY lease_expires_at, id LIMIT 1" + lock_rows(),
            (*LIVE, _iso(moment))).fetchone()
        if dead is None:
            return None
        run_id, was = dead["id"], RunStatus(dead["status"])
        reason = f"worker {dead['lease_owner']} stopped renewing its lease"
        if was is RunStatus.CANCEL_REQUESTED:  # the user already wanted it stopped: honour that, do not resume
            transition(conn, run_id, RunStatus.CANCELLED, actor="recovery", reason=reason, attempt=dead["attempt"],
                       fields={"outcome": "cancelled"})
        else:
            transition(conn, run_id, RunStatus.INTERRUPTED, actor="recovery", reason=reason, attempt=dead["attempt"],
                       fields={"outcome": "interrupted"})
            ledger.append(conn, run_id, "run_interrupted", phase=dead["current_phase"], status="interrupted", message=reason,
                          actor="recovery", attempt=dead["attempt"])
        conn.execute("UPDATE approvals SET status = 'expired', resolved_at = ? WHERE run_id = ? AND status = 'pending'",
                     (ledger.now_iso(), run_id))
        conn.execute("UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL WHERE id = ?", (run_id,))
        return Claim(run_id, "recover", 0, dead["attempt"])


def heartbeat(run_id: str, worker_id: str, epoch: int, lease_s: float, *, now: datetime | None = None) -> None:
    moment = now or datetime.fromisoformat(ledger.now_iso())
    with get_db() as conn:
        cur = conn.execute("UPDATE runs SET lease_expires_at = ? WHERE id = ? AND lease_owner = ? AND lease_epoch = ?",
                           (_iso(moment + timedelta(seconds=lease_s)), run_id, worker_id, epoch))
        if cur.rowcount != 1:
            raise LeaseLost(f"worker {worker_id} no longer holds run {run_id}")


def release(run_id: str, worker_id: str, epoch: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL WHERE id = ? AND lease_owner = ? AND lease_epoch = ?",
                     (run_id, worker_id, epoch))


def stats(*, now: datetime | None = None) -> dict[str, int | float | None]:
    moment = _iso(now or datetime.fromisoformat(ledger.now_iso()))
    with get_db() as conn:
        queued = conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'queued'").fetchone()[0]
        leased = conn.execute("SELECT COUNT(*) FROM runs WHERE lease_owner IS NOT NULL AND lease_expires_at >= ?", (moment,)).fetchone()[0]
        expired = conn.execute("SELECT COUNT(*) FROM runs WHERE lease_owner IS NOT NULL AND lease_expires_at < ?", (moment,)).fetchone()[0]
        oldest = conn.execute("SELECT MIN(queued_at) FROM runs WHERE status = 'queued'").fetchone()[0]
    wait = None if oldest is None else max(0.0, (datetime.fromisoformat(moment) - datetime.fromisoformat(oldest)).total_seconds())
    return {"queued": queued, "leased": leased, "expired_leases": expired, "oldest_wait_s": wait}

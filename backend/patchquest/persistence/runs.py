"""Run status changes: validated, compare-and-set, and recorded in the ledger."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from patchquest.domain.runs import TERMINAL, IllegalTransition, RunStatus, StaleTransition, can_transition
from patchquest.persistence import ledger

# Columns a transition may set alongside the status (everything else goes through its own writer).
_SETTABLE = frozenset({"outcome", "verdict", "current_phase"})


@dataclass(frozen=True)
class Transition:
    previous: RunStatus
    event_id: int
    event_uid: str


def transition(
    conn: sqlite3.Connection,
    run_id: str,
    target: RunStatus,
    *,
    actor: str,
    reason: str,
    attempt: int = 1,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Transition:
    """Move ``run_id`` to ``target`` and record the change. Returns the previous status and the recorded event.

    Raises ``IllegalTransition`` if the table forbids it and ``StaleTransition`` if the row changed
    underneath us. Runs inside the caller's transaction, so the status and its event commit together.
    """
    row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise LookupError(run_id)
    current = RunStatus(row["status"])
    if not can_transition(current, target):
        raise IllegalTransition(run_id, current, target)

    extra = {k: v for k, v in (fields or {}).items() if k in _SETTABLE}
    sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in extra]
    params: list[Any] = [target.value, ledger.now_iso(), *extra.values()]
    if target in TERMINAL or target is RunStatus.INTERRUPTED:
        sets.append("completed_at = ?")
        params.append(ledger.now_iso())
    elif current is RunStatus.INTERRUPTED:
        sets.append("completed_at = NULL")  # resumed: no longer finished
    cur = conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ? AND status = ?",
                       (*params, run_id, current.value))
    if cur.rowcount != 1:
        raise StaleTransition(f"run {run_id} changed concurrently while moving {current} -> {target}")

    event_id, event_uid = ledger.append(
        conn, run_id, "run_state_changed", status=target.value, message=reason,
        payload={"from": current.value, "to": target.value}, actor=actor, attempt=attempt,
        correlation_id=correlation_id, causation_id=causation_id)
    return Transition(current, event_id, event_uid)

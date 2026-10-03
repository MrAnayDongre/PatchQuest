"""Approval requests, decisions and run-scoped grants.

A decision is a compare-and-set on a *pending, unexpired* request, so the first decision wins and a late,
duplicate or forged one changes nothing. The decision and its ledger event commit together.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from patchquest.domain.approvals import (
    APPROVING,
    STATUS_FOR,
    AlreadyDecided,
    ApprovalNotFound,
    ApprovalStatus,
    Decision,
    DecisionNotAllowed,
)
from patchquest.domain.effects import GRANTABLE, SideEffect
from patchquest.persistence import ledger


def signature(command: str) -> str:
    """Stable identity of a command for grants: the parsed argv, so spacing and quoting do not matter."""
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = [command]
    return hashlib.sha256(json.dumps(argv).encode()).hexdigest()


def request(conn: sqlite3.Connection, run_id: str, *, kind: str, reason: str, command: str | None = None,
            side_effect: SideEffect = SideEffect.UNKNOWN, risk: str | None = None, phase: str | None = None,
            requested_by: str = "runtime", timeout_s: float | None = None,
            resources: list[str] | None = None) -> str:
    approval_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    conn.execute(
        """INSERT INTO approvals (id, run_id, type, command, reason, status, created_at, side_effect, risk, phase,
               requested_by, expires_at, resources_json) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
        (approval_id, run_id, kind, command, reason, now.isoformat(), side_effect.value, risk, phase, requested_by,
         (now + timedelta(seconds=timeout_s)).isoformat() if timeout_s is not None else None,
         json.dumps(resources) if resources else None))
    return approval_id


@dataclass(frozen=True)
class Outcome:
    approval_id: str
    decision: Decision
    status: ApprovalStatus
    command: str | None  # the command to run: the approver's edit for MODIFY, else the original

    @property
    def approved(self) -> bool:
        return self.decision in APPROVING


def decide(conn: sqlite3.Connection, run_id: str, approval_id: str, decision: Decision, *, actor: str,
           note: str | None = None, modified_command: str | None = None) -> Outcome:
    """Record ``decision``. Raises ``ApprovalNotFound``, ``AlreadyDecided`` or ``DecisionNotAllowed``."""
    row = conn.execute("SELECT * FROM approvals WHERE id = ? AND run_id = ?", (approval_id, run_id)).fetchone()
    if row is None:
        raise ApprovalNotFound(f"no approval {approval_id} on this run")
    if row["status"] != ApprovalStatus.PENDING:
        raise AlreadyDecided(f"this approval was already {row['status']}")
    if row["expires_at"] and row["expires_at"] <= ledger.now_iso():
        # Marking it expired is the waiting run's job (``expire``); here a late decision is simply refused.
        raise AlreadyDecided("this approval expired before a decision was made")

    effect = SideEffect(row["side_effect"] or SideEffect.UNKNOWN)
    if decision is Decision.APPROVE_FOR_RUN and (effect not in GRANTABLE or not row["command"]):
        raise DecisionNotAllowed(f"a {effect.value} action cannot be approved for the whole run; approve it once")
    if decision is Decision.MODIFY and (row["type"] != "command" or not (modified_command or "").strip()):
        raise DecisionNotAllowed("only a command approval can be modified, and it needs the modified command")

    status = STATUS_FOR[decision]
    cur = conn.execute("UPDATE approvals SET status = ?, decision = ?, note = ?, resolved_at = ?, decided_by = ?, "
                       "modified_command = ? WHERE id = ? AND status = 'pending'",
                       (status.value, decision.value, note, ledger.now_iso(), actor,
                        modified_command if decision is Decision.MODIFY else None, approval_id))
    if cur.rowcount != 1:  # someone decided between our read and write
        raise AlreadyDecided("this approval was decided at the same moment by someone else")
    if decision is Decision.APPROVE_FOR_RUN:
        conn.execute("INSERT OR IGNORE INTO approval_grants (run_id, signature, side_effect, approval_id, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (run_id, signature(row["command"]), effect.value, approval_id, ledger.now_iso()))
    ledger.append(conn, run_id, "approval_decided", phase=row["phase"], status=status.value, actor=actor,
                  message=f"{decision.value}: {row['reason'] or row['type']}",
                  payload={"approval_id": approval_id, "decision": decision.value, "side_effect": effect.value,
                           "note": note, "modified": decision is Decision.MODIFY})
    command = modified_command if decision is Decision.MODIFY else row["command"]
    return Outcome(approval_id, decision, status, command)


def expire(conn: sqlite3.Connection, approval_id: str) -> bool:
    """Mark a pending request expired. False if it was already decided (the decision stands)."""
    cur = conn.execute("UPDATE approvals SET status = 'expired', resolved_at = ? WHERE id = ? AND status = 'pending'",
                       (ledger.now_iso(), approval_id))
    return cur.rowcount == 1


def has_grant(conn: sqlite3.Connection, run_id: str, command: str) -> bool:
    return conn.execute("SELECT 1 FROM approval_grants WHERE run_id = ? AND signature = ?",
                        (run_id, signature(command))).fetchone() is not None


def pending(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM approvals WHERE run_id = ? AND status = 'pending' "
                        "AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at", (run_id, ledger.now_iso())).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["grantable"] = bool(r["command"]) and r["type"] == "command" and SideEffect(r["side_effect"] or SideEffect.UNKNOWN) in GRANTABLE
        out.append(d)
    return out

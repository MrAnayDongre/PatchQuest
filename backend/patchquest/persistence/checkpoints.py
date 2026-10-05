"""Durable, checksummed, versioned run checkpoints.

A checkpoint is one SQLite row written in one transaction, so it is either wholly there or not at
all. On top of that every row carries a SHA-256 over its identity and content: a truncated or
bit-rotted row is *detected* on load and skipped in favour of the previous good checkpoint, never
trusted. Rows written by an older release are upgraded on read; rows from a newer one are refused.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from patchquest.persistence.ledger import now_iso

CHECKPOINT_SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024

# version N -> function turning a version-N state dict into version N+1.
UPGRADERS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


class CheckpointError(RuntimeError):
    pass


class CheckpointCorrupt(CheckpointError):
    pass


class CheckpointTooNew(CheckpointError):
    pass


class CheckpointTooLarge(CheckpointError):
    pass


@dataclass(frozen=True)
class Checkpoint:
    id: str
    run_id: str
    seq: int
    schema_version: int
    runtime_version: str
    phase: str  # the last phase fully completed when this was taken
    event_cursor: int  # ledger position the state corresponds to
    attempt: int
    created_at: str
    state: dict[str, Any]
    fingerprint: dict[str, Any]


def _checksum(run_id: str, seq: int, schema_version: int, phase: str, event_cursor: int, attempt: int,
              state_json: str, fingerprint_json: str) -> str:
    head = json.dumps([run_id, seq, schema_version, phase, event_cursor, attempt], separators=(",", ":"))
    digest = hashlib.sha256()
    for part in (head, state_json, fingerprint_json):
        digest.update(part.encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def save(conn: sqlite3.Connection, *, run_id: str, phase: str, state: dict[str, Any], fingerprint: dict[str, Any],
         event_cursor: int, attempt: int, runtime_version: str) -> Checkpoint:
    state_json, fingerprint_json = _dump(state), _dump(fingerprint)
    if len(state_json) > MAX_CHECKPOINT_BYTES:
        raise CheckpointTooLarge(f"checkpoint state is {len(state_json) // 2**20} MiB (limit {MAX_CHECKPOINT_BYTES // 2**20})")
    seq = int(conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM checkpoints WHERE run_id = ?", (run_id,)).fetchone()[0])
    cp_id, created = uuid.uuid4().hex, now_iso()
    conn.execute(
        """INSERT INTO checkpoints (id, run_id, seq, schema_version, runtime_version, phase, event_cursor, attempt,
               state_json, fingerprint_json, checksum, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cp_id, run_id, seq, CHECKPOINT_SCHEMA_VERSION, runtime_version, phase, event_cursor, attempt, state_json,
         fingerprint_json, _checksum(run_id, seq, CHECKPOINT_SCHEMA_VERSION, phase, event_cursor, attempt,
                                     state_json, fingerprint_json), created))
    return Checkpoint(cp_id, run_id, seq, CHECKPOINT_SCHEMA_VERSION, runtime_version, phase, event_cursor, attempt,
                      created, state, fingerprint)


def _decode(row: sqlite3.Row) -> Checkpoint:
    """Verify and decode one row. Raises ``CheckpointCorrupt`` / ``CheckpointTooNew``."""
    where = f"checkpoint {row['seq']} of run {row['run_id']}"
    state_json, fingerprint_json = row["state_json"], row["fingerprint_json"]
    if not isinstance(state_json, str) or not isinstance(fingerprint_json, str):
        raise CheckpointCorrupt(f"{where}: content missing")
    expected = _checksum(row["run_id"], row["seq"], row["schema_version"], row["phase"], row["event_cursor"],
                         row["attempt"], state_json, fingerprint_json)
    if expected != row["checksum"]:
        raise CheckpointCorrupt(f"{where}: checksum mismatch (content was truncated or modified)")
    version = row["schema_version"]
    if version > CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointTooNew(f"{where}: written by a newer PatchQuest (schema v{version})")
    try:
        state, fingerprint = json.loads(state_json), json.loads(fingerprint_json)
    except json.JSONDecodeError as exc:
        raise CheckpointCorrupt(f"{where}: unreadable content ({exc})") from exc
    while version < CHECKPOINT_SCHEMA_VERSION:
        upgrade = UPGRADERS.get(version)
        if upgrade is None:
            raise CheckpointCorrupt(f"{where}: no upgrade path from schema v{version}")
        state, version = upgrade(state), version + 1
    return Checkpoint(row["id"], row["run_id"], row["seq"], version, row["runtime_version"], row["phase"],
                      row["event_cursor"], row["attempt"], row["created_at"], state, fingerprint)


def latest_valid(conn: sqlite3.Connection, run_id: str) -> tuple[Checkpoint | None, list[str]]:
    """The newest checkpoint that verifies, plus a description of every newer one that did not."""
    problems: list[str] = []
    for row in conn.execute("SELECT * FROM checkpoints WHERE run_id = ? ORDER BY seq DESC", (run_id,)):
        try:
            return _decode(row), problems
        except CheckpointError as exc:
            problems.append(str(exc))
    return None, problems


def get(conn: sqlite3.Connection, run_id: str, seq: int) -> Checkpoint:
    row = conn.execute("SELECT * FROM checkpoints WHERE run_id = ? AND seq = ?", (run_id, seq)).fetchone()
    if row is None:
        raise LookupError(f"run {run_id} has no checkpoint {seq}")
    return _decode(row)


def describe(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """One summary per checkpoint (oldest first), with integrity status, without loading state."""
    out = []
    for row in conn.execute("SELECT * FROM checkpoints WHERE run_id = ? ORDER BY seq", (run_id,)):
        try:
            _decode(row)
            status = "ok"
        except CheckpointError as exc:
            status = f"invalid: {exc}"
        out.append({"seq": row["seq"], "phase": row["phase"], "attempt": row["attempt"], "event_cursor": row["event_cursor"],
                    "schema_version": row["schema_version"], "created_at": row["created_at"],
                    "bytes": len(row["state_json"] or ""), "status": status})
    return out

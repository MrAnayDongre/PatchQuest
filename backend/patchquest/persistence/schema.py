"""The migration list. Append new migrations; never edit one that has shipped."""

from __future__ import annotations

import sqlite3

from patchquest.persistence.migrations import Migration, add_columns, run_script

_LEDGER_COLUMNS = {
    "event_uid": "TEXT",
    "schema_version": "INTEGER NOT NULL DEFAULT 1",
    "actor": "TEXT",
    "attempt": "INTEGER NOT NULL DEFAULT 1",
    "correlation_id": "TEXT",
    "causation_id": "TEXT",
    "redacted": "INTEGER NOT NULL DEFAULT 0",
}

# Updates are refused unless they are exactly a redaction (content blanked, flag set, identity untouched).
_LEDGER_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS run_events_no_delete BEFORE DELETE ON run_events
BEGIN SELECT RAISE(ABORT, 'run_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS run_events_no_update BEFORE UPDATE ON run_events
WHEN NOT (NEW.redacted = 1 AND NEW.payload_json IS NULL AND NEW.message IS NULL
          AND NEW.id = OLD.id AND NEW.run_id = OLD.run_id AND NEW.type = OLD.type
          AND NEW.created_at = OLD.created_at AND NEW.event_uid IS OLD.event_uid)
BEGIN SELECT RAISE(ABORT, 'run_events is append-only'); END;
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_uid ON run_events(event_uid) WHERE event_uid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_run_cursor ON run_events(run_id, id);
"""


def _baseline(conn: sqlite3.Connection) -> None:
    from patchquest.database import LEGACY_RUN_COLUMNS, LEGACY_SCHEDULE_COLUMNS, SCHEMA

    run_script(conn, SCHEMA)
    add_columns(conn, "runs", LEGACY_RUN_COLUMNS)
    add_columns(conn, "scheduled_tasks", LEGACY_SCHEDULE_COLUMNS)


def _ledger(conn: sqlite3.Connection) -> None:
    add_columns(conn, "run_events", _LEDGER_COLUMNS)
    add_columns(conn, "runs", {"attempt": "INTEGER NOT NULL DEFAULT 1"})
    run_script(conn, _LEDGER_TRIGGERS)


_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    seq INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    runtime_version TEXT NOT NULL,
    phase TEXT NOT NULL,
    event_cursor INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, seq)
);
"""


def _checkpoints(conn: sqlite3.Connection) -> None:
    run_script(conn, _CHECKPOINTS)


def _failure_kind(conn: sqlite3.Connection) -> None:
    add_columns(conn, "runs", {"failure_kind": "TEXT"})
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_failure ON runs(failure_kind) WHERE failure_kind IS NOT NULL")


def _lineage(conn: sqlite3.Connection) -> None:
    add_columns(conn, "runs", {
        "parent_run_id": "TEXT REFERENCES runs(id)",
        "parent_checkpoint_seq": "INTEGER",
        "lineage_kind": "TEXT",  # 'fork' | 'replay'
        "replay_mode": "TEXT",  # 'model' | 'live'
        "overrides_json": "TEXT",  # per-run settings, re-applied on resume
    })
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id) WHERE parent_run_id IS NOT NULL")


def _approvals(conn: sqlite3.Connection) -> None:
    add_columns(conn, "approvals", {
        "side_effect": "TEXT", "risk": "TEXT", "phase": "TEXT", "requested_by": "TEXT", "expires_at": "TEXT",
        "decision": "TEXT", "decided_by": "TEXT", "modified_command": "TEXT", "resources_json": "TEXT",
    })
    run_script(conn, """
        CREATE TABLE IF NOT EXISTS approval_grants (
            run_id TEXT NOT NULL REFERENCES runs(id),
            signature TEXT NOT NULL,
            side_effect TEXT NOT NULL,
            approval_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, signature)
        );
        CREATE INDEX IF NOT EXISTS idx_approvals_run_status ON approvals(run_id, status);
    """)
    # Older rows said 'rejected'; the vocabulary is now 'denied'.
    conn.execute("UPDATE approvals SET status = 'denied' WHERE status = 'rejected'")


MIGRATIONS = [
    Migration(1, "baseline schema", _baseline),
    Migration(2, "versioned immutable event ledger", _ledger),
    Migration(3, "checkpoints", _checkpoints),
    Migration(4, "run failure kind", _failure_kind),
    Migration(5, "run lineage and per-run overrides", _lineage),
    Migration(6, "approval decisions, expiry and grants", _approvals),
]

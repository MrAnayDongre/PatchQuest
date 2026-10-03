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


MIGRATIONS = [
    Migration(1, "baseline schema", _baseline),
    Migration(2, "versioned immutable event ledger", _ledger),
]

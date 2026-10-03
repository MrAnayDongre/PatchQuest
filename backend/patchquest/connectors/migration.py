"""Schema for connector dedup and outbound delivery. Idempotent, so it is safe to re-apply."""

from __future__ import annotations

import sqlite3

from patchquest.persistence.migrations import Migration, run_script

_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_events (
    workspace_id TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RECEIVED', 'STARTED', 'IGNORED')),
    run_id TEXT NULL,
    PRIMARY KEY (workspace_id, source, external_id)
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    event_type TEXT NOT NULL,
    body_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'DELIVERED', 'DEAD')),
    last_error TEXT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due ON webhook_deliveries(status, next_attempt_at);
"""


def apply_fn(conn: sqlite3.Connection) -> None:
    run_script(conn, _SCHEMA)


MIGRATION = Migration(8, "connector events and webhook deliveries", apply_fn)

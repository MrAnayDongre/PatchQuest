"""The migration list. Append new migrations; never edit one that has shipped."""

from __future__ import annotations

import sqlite3

from patchquest.connectors.migration import MIGRATION as CONNECTOR_MIGRATION
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


def _identity(conn: sqlite3.Connection) -> None:
    run_script(conn, """
        CREATE TABLE IF NOT EXISTS organizations (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id), name TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE (org_id, name));
        CREATE TABLE IF NOT EXISTS principals (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
            kind TEXT NOT NULL CHECK (kind IN ('user', 'service')), name TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memberships (
            principal_id TEXT NOT NULL REFERENCES principals(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            role TEXT NOT NULL, PRIMARY KEY (principal_id, workspace_id));
        CREATE TABLE IF NOT EXISTS api_tokens (
            id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals(id), token_hash TEXT NOT NULL UNIQUE,
            prefix TEXT NOT NULL, label TEXT, created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, last_used_at TEXT);
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, org_id TEXT, workspace_id TEXT, actor TEXT NOT NULL,
            action TEXT NOT NULL, target TEXT, outcome TEXT NOT NULL, detail_json TEXT, remote TEXT);
        CREATE INDEX IF NOT EXISTS idx_audit_ws ON audit_log(workspace_id, id);
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    """)
    add_columns(conn, "runs", {"workspace_id": "TEXT NOT NULL DEFAULT 'ws_local'", "created_by": "TEXT"})
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs(workspace_id, created_at)")
    # Single-user local mode lives in one implicit organisation and workspace; existing runs belong to it.
    now = "datetime('now')"
    conn.execute(f"INSERT OR IGNORE INTO organizations (id, name, created_at) VALUES ('org_local', 'Local', {now})")
    conn.execute("INSERT OR IGNORE INTO workspaces (id, org_id, name, created_at) "
                 f"VALUES ('ws_local', 'org_local', 'Local', {now})")


def _workflows(conn: sqlite3.Connection) -> None:
    run_script(conn, """
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL,
            version INTEGER NOT NULL, definition_json TEXT NOT NULL, trigger_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', created_by TEXT, created_at TEXT NOT NULL,
            UNIQUE (workspace_id, name, version));
        CREATE INDEX IF NOT EXISTS idx_workflows_trigger ON workflows(workspace_id, trigger_type, status);
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id), workspace_id TEXT NOT NULL,
            status TEXT NOT NULL, trigger_json TEXT NOT NULL, trigger_key TEXT, vars_json TEXT NOT NULL,
            created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, error TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wfruns_trigger ON workflow_runs(workflow_id, trigger_key) WHERE trigger_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_wfruns_ws ON workflow_runs(workspace_id, created_at);
        CREATE TABLE IF NOT EXISTS workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
            node_id TEXT NOT NULL, visit INTEGER NOT NULL, status TEXT NOT NULL, output_json TEXT, error TEXT,
            started_at TEXT, finished_at TEXT, wait_kind TEXT, wait_key TEXT, wake_at TEXT, child_run_id TEXT,
            idempotency_key TEXT, decision TEXT, decided_by TEXT, UNIQUE (workflow_run_id, node_id, visit));
        CREATE INDEX IF NOT EXISTS idx_wfsteps_wait ON workflow_steps(status, wait_kind, wait_key);
        CREATE TABLE IF NOT EXISTS workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
            type TEXT NOT NULL, node_id TEXT, actor TEXT NOT NULL, message TEXT, payload_json TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_wfevents_run ON workflow_events(workflow_run_id, id);
        CREATE TRIGGER IF NOT EXISTS workflow_events_no_update BEFORE UPDATE ON workflow_events
            BEGIN SELECT RAISE(ABORT, 'workflow_events is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS workflow_events_no_delete BEFORE DELETE ON workflow_events
            BEGIN SELECT RAISE(ABORT, 'workflow_events is append-only'); END;
    """)


def _leases(conn: sqlite3.Connection) -> None:
    add_columns(conn, "runs", {
        "queued_at": "TEXT",
        "lease_owner": "TEXT",
        "lease_expires_at": "TEXT",
        "lease_epoch": "INTEGER NOT NULL DEFAULT 0",  # bumped on every claim; identifies which owner a write came from
        "resume_note": "TEXT",  # what a deferred resume decided (e.g. "applied": the patch had already landed)
    })
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_queue ON runs(status, queued_at)")


def _policies(conn: sqlite3.Connection) -> None:
    run_script(conn, """
        CREATE TABLE IF NOT EXISTS policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope INTEGER NOT NULL, scope_ref TEXT NOT NULL, name TEXT NOT NULL,
            version INTEGER NOT NULL, document_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (scope, scope_ref, name, version));
        CREATE INDEX IF NOT EXISTS idx_policies_active ON policies(active, scope, scope_ref);
        CREATE TRIGGER IF NOT EXISTS policies_no_delete BEFORE DELETE ON policies
            BEGIN SELECT RAISE(ABORT, 'policies keep their history; disable instead'); END;
    """)


MIGRATIONS = [
    Migration(1, "baseline schema", _baseline),
    Migration(2, "versioned immutable event ledger", _ledger),
    Migration(3, "checkpoints", _checkpoints),
    Migration(4, "run failure kind", _failure_kind),
    Migration(5, "run lineage and per-run overrides", _lineage),
    Migration(6, "approval decisions, expiry and grants", _approvals),
    Migration(7, "organisations, workspaces, principals, tokens and audit log", _identity),
    CONNECTOR_MIGRATION,
    Migration(9, "workflows, runs, steps and events", _workflows),
    Migration(10, "run queue and worker leases", _leases),
    Migration(11, "scoped, versioned policies", _policies),
]

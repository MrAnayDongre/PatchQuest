"""Plugin enablement state and the append-only record of what plugins did."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from patchquest.persistence.ledger import now_iso


def get(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM plugin_state WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    d = {k: row[k] for k in row.keys()}
    d["granted"], d["config"] = json.loads(d.pop("granted_json")), json.loads(d.pop("config_json"))
    return d


def list_state(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [s for r in conn.execute("SELECT name FROM plugin_state ORDER BY name") if (s := get(conn, r["name"])) is not None]


def save(conn: sqlite3.Connection, name: str, version: str, state: str, granted: list[str], config: dict[str, Any], actor: str) -> None:
    conn.execute(
        "INSERT INTO plugin_state (name, version, state, granted_json, config_json, consecutive_failures, last_error, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?) ON CONFLICT(name) DO UPDATE SET version = excluded.version, state = excluded.state, "
        "granted_json = excluded.granted_json, config_json = excluded.config_json, consecutive_failures = 0, last_error = NULL, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (name, version, state, json.dumps(sorted(granted)), json.dumps(config, sort_keys=True), now_iso(), actor))


def set_state(conn: sqlite3.Connection, name: str, state: str, actor: str) -> None:
    conn.execute("UPDATE plugin_state SET state = ?, updated_at = ?, updated_by = ? WHERE name = ?", (state, now_iso(), actor, name))


def record_outcome(conn: sqlite3.Connection, name: str, ok: bool, error: str | None, quarantine_after: int) -> bool:
    """Track consecutive failures; returns True when this failure quarantined the plugin."""
    if ok:
        conn.execute("UPDATE plugin_state SET consecutive_failures = 0 WHERE name = ?", (name,))
        return False
    conn.execute("UPDATE plugin_state SET consecutive_failures = consecutive_failures + 1, last_error = ? WHERE name = ?",
                 ((error or "")[:300], name))
    row = conn.execute("SELECT consecutive_failures, state FROM plugin_state WHERE name = ?", (name,)).fetchone()
    if row and row["consecutive_failures"] >= quarantine_after and row["state"] == "enabled":
        conn.execute("UPDATE plugin_state SET state = 'quarantined', updated_at = ? WHERE name = ?", (now_iso(), name))
        return True
    return False


def event(conn: sqlite3.Connection, plugin: str, type_: str, *, capability: str | None = None, outcome: str = "ok",
          duration_ms: int | None = None, detail: dict[str, Any] | None = None) -> None:
    conn.execute("INSERT INTO plugin_events (ts, plugin, type, capability, outcome, duration_ms, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (now_iso(), plugin, type_, capability, outcome, duration_ms, json.dumps(detail, default=str) if detail else None))


def events(conn: sqlite3.Connection, plugin: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM plugin_events WHERE plugin = ? ORDER BY id DESC LIMIT ?", (plugin, limit)).fetchall()
    return [{**{k: r[k] for k in r.keys() if k != "detail_json"}, "detail": json.loads(r["detail_json"]) if r["detail_json"] else None}
            for r in rows]

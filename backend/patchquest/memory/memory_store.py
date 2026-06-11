"""Memory record CRUD operations."""

from __future__ import annotations

import json
from typing import Any

from patchquest.database import get_db, now_iso


def save_memory(scope: str, record_type: str, key: str, value: Any,
                source_path: str | None = None, file_hash: str | None = None) -> int:
    from patchquest.tools.secret_guard import has_secrets
    value_str = json.dumps(value) if not isinstance(value, str) else value
    if has_secrets(value_str):
        return -1

    now = now_iso()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO memory_records (scope, record_type, key, value_json, source_path, file_hash, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'fresh', ?, ?)""",
            (scope, record_type, key, json.dumps(value), source_path, file_hash, now, now),
        )
        return cursor.lastrowid  # type: ignore


def get_memory(scope: str | None = None, record_type: str | None = None,
               status: str = "fresh") -> list[dict]:
    query = "SELECT * FROM memory_records WHERE status = ?"
    params: list[Any] = [status]
    if scope:
        query += " AND scope = ?"
        params.append(scope)
    if record_type:
        query += " AND record_type = ?"
        params.append(record_type)
    query += " ORDER BY updated_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def invalidate_by_path(source_path: str) -> int:
    now = now_iso()
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE memory_records SET status = 'invalid', updated_at = ? WHERE source_path = ?",
            (now, source_path),
        )
        return cursor.rowcount


def mark_stale_by_hash(source_path: str, old_hash: str) -> int:
    now = now_iso()
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE memory_records SET status = 'stale', updated_at = ? WHERE source_path = ? AND file_hash = ?",
            (now, source_path, old_hash),
        )
        return cursor.rowcount

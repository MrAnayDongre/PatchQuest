"""Versioned, forward-only schema migrations.

``schema_migrations`` records what has been applied. Each migration runs in one transaction, so a
crash leaves the database at the previous version. A database written by a *newer* PatchQuest is
refused rather than silently downgraded. Before the first pending migration touches an existing
database a consistent backup is written next to it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class SchemaTooNew(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def statements(script: str) -> Iterator[str]:
    """Split a SQL script into statements (``executescript`` would commit mid-migration)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""
    if buf.strip():
        yield buf.strip()


def run_script(conn: sqlite3.Connection, script: str) -> None:
    for stmt in statements(script):
        conn.execute(stmt)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    present = _columns(conn, table)
    for name, definition in columns.items():
        if name not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                 "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def _has_user_data(conn: sqlite3.Connection) -> bool:
    """True for a database worth backing up: migrated before, or created by a pre-migration release."""
    if current_version(conn) > 0:
        return True
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'").fetchone() is not None


def pending(conn: sqlite3.Connection, migrations: list[Migration]) -> list[Migration]:
    current = current_version(conn)
    latest = max((m.version for m in migrations), default=0)
    if current > latest:
        raise SchemaTooNew(
            f"database schema is v{current} but this PatchQuest understands up to v{latest}; upgrade PatchQuest")
    return [m for m in sorted(migrations, key=lambda m: m.version) if m.version > current]


def backup_database(conn: sqlite3.Connection, db_path: Path, version: int) -> Path:
    target = db_path.with_name(f"{db_path.name}.pre-v{version}.bak")
    dest = sqlite3.connect(str(target))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


def migrate(conn: sqlite3.Connection, migrations: list[Migration], db_path: Path | None = None) -> list[int]:
    """Apply pending migrations in order. Returns the versions applied."""
    todo = pending(conn, migrations)
    applied: list[int] = []
    for migration in todo:
        if db_path is not None and not applied and _has_user_data(conn):
            backup_database(conn, db_path, migration.version)
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration.apply(conn)
            conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                         (migration.version, migration.name, datetime.now(UTC).isoformat()))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        applied.append(migration.version)
    return applied

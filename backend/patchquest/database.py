"""SQLite database initialization and access."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from patchquest.config import get_config
from patchquest.persistence import ledger

_DB_PATH: Path | None = None
BUSY_TIMEOUT_S = 10.0  # wait for a competing writer instead of failing with 'database is locked'


def resolve_db_path(configured: str | None) -> Path:
    env = os.environ.get("PATCHQUEST_DB")
    if env:
        return Path(env).expanduser()
    if configured:
        return Path(configured).expanduser()
    legacy = Path("patchquest.db")
    if legacy.exists():  # keep working for installs that predate the per-user location
        return legacy
    return Path.home() / ".patchquest" / "patchquest.db"


def get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = resolve_db_path(get_config().db_path)
    return _DB_PATH


def set_db_path(path: Path) -> None:
    global _DB_PATH
    _DB_PATH = path


_PG_DSN: str | None = None
_PG_SCHEMA: str | None = None
INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)


def _env_postgres() -> tuple[str, str | None] | None:
    dsn = os.environ.get("PATCHQUEST_DATABASE_URL")
    return (dsn, os.environ.get("PATCHQUEST_DATABASE_SCHEMA")) if dsn else None


def use_postgres(dsn: str, schema: str | None = None) -> None:
    """Use PostgreSQL (team / server mode) instead of the SQLite file. ``schema`` namespaces a install inside a database."""
    global _PG_DSN, _PG_SCHEMA, INTEGRITY_ERRORS
    _PG_DSN, _PG_SCHEMA = dsn, schema
    from patchquest import dbpg

    INTEGRITY_ERRORS = (sqlite3.IntegrityError, dbpg.IntegrityError)


def use_sqlite() -> None:
    global _PG_DSN, _PG_SCHEMA, INTEGRITY_ERRORS
    if _PG_DSN:
        from patchquest import dbpg

        dbpg.close_pool(_PG_DSN, _PG_SCHEMA)
    _PG_DSN = _PG_SCHEMA = None
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)


_FORCE_SQLITE = False


@contextmanager
def isolated_sqlite(path: Path) -> Generator[None, None, None]:
    """A throwaway SQLite database for the duration, whatever the install normally uses (evaluation and recovery
    experiments must never write into real run history, and a PostgreSQL install must not be touched by them)."""
    global _FORCE_SQLITE, _DB_PATH
    saved_force, saved_path = _FORCE_SQLITE, _DB_PATH
    _FORCE_SQLITE, _DB_PATH = True, path
    try:
        init_db()
        yield
    finally:
        _FORCE_SQLITE, _DB_PATH = saved_force, saved_path


def child_environment() -> dict[str, str]:
    """Environment that makes a child process use the same database as this one."""
    target = postgres_target()
    if target is None:
        return {"PATCHQUEST_DB": str(get_db_path())}
    env = {"PATCHQUEST_DATABASE_URL": target[0]}
    if target[1]:
        env["PATCHQUEST_DATABASE_SCHEMA"] = target[1]
    return env


def postgres_target() -> tuple[str, str | None] | None:
    """(dsn, schema) when running on PostgreSQL, else None. An explicit ``use_postgres`` wins over the environment."""
    if _FORCE_SQLITE:
        return None
    if _PG_DSN:
        return _PG_DSN, _PG_SCHEMA
    env = _env_postgres()
    if env:
        use_postgres(*env)
        return env
    return None


def describe_target() -> str:
    """Where the data lives, safe to print: the SQLite path, or host:port/database with no credentials."""
    target = postgres_target()
    if target is None:
        return str(get_db_path())
    from urllib.parse import urlsplit

    parts = urlsplit(target[0])
    return f"postgresql://{parts.hostname}:{parts.port or 5432}{parts.path}" + (f" (schema {target[1]})" if target[1] else "")


def database_exists() -> bool:
    return is_postgres() or get_db_path().exists()


def is_postgres() -> bool:
    return postgres_target() is not None


def begin_write(conn: Any) -> None:
    """Start a transaction that no other writer can overlap (SQLite: ``BEGIN IMMEDIATE``; PostgreSQL: advisory lock)."""
    conn.execute("BEGIN IMMEDIATE")


def lock_rows() -> str:
    """Suffix for a SELECT that claims rows other workers must skip instead of waiting for."""
    return " FOR UPDATE SKIP LOCKED" if is_postgres() else ""


def serialize(conn: Any, key: str) -> None:
    """Make writers using the same ``key`` take turns until commit (PostgreSQL only; SQLite writers already do)."""
    if getattr(conn, "dialect", None) == "postgresql":
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (key,))


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    target = postgres_target()
    if target is not None:
        from patchquest import dbpg

        with dbpg.connect(*target) as pg:
            yield pg  # type: ignore[misc]
        return
    conn = sqlite3.connect(str(get_db_path()), timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL: durable across process crashes, fast commits
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    from patchquest.persistence.migrations import migrate
    from patchquest.persistence.schema import MIGRATIONS

    if is_postgres():
        with get_db() as conn:
            migrate(conn, MIGRATIONS, None)
        from patchquest.memory.code_graph import init_code_graph as _init

        _init()
        return
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _make_private(db_path)
    with get_db() as conn:
        migrate(conn, MIGRATIONS, db_path)
    from patchquest.memory.code_graph import init_code_graph
    init_code_graph()


def ensure_state_dir() -> Path:
    """``~/.patchquest``, created owner-only. Run history, checkpoints and shadow workspaces hold task text, command
    output and file contents, so the directory PatchQuest owns is never group- or world-readable."""
    state_dir = Path.home() / ".patchquest"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_dir.stat().st_mode & 0o077:
        os.chmod(state_dir, 0o700)
    return state_dir


def _make_private(db_path: Path) -> None:
    """Keep PatchQuest's own state directory and database owner-only. A directory the user chose elsewhere is theirs to
    manage and is left alone."""
    if db_path.parent.resolve() == (Path.home() / ".patchquest").resolve():
        ensure_state_dir()
        if db_path.exists():
            os.chmod(db_path, 0o600)


# Columns older databases lack; migration 1 adds them.
LEGACY_RUN_COLUMNS = {
    "provider": "TEXT DEFAULT 'mock'",
    "model": "TEXT",
    "runtime_mode": "TEXT DEFAULT 'local'",
    "outcome": "TEXT",
    "verdict": "TEXT",
    "base_url": "TEXT",
}
LEGACY_SCHEDULE_COLUMNS = {"provider": "TEXT DEFAULT 'mock'", "model": "TEXT"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    current_phase TEXT,
    provider TEXT DEFAULT 'mock',
    model TEXT,
    model_profile TEXT,
    memory_mode TEXT DEFAULT 'repo',
    runtime_mode TEXT DEFAULT 'local',
    allow_network INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT,
    verdict TEXT,
    base_url TEXT
);

CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    role TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    attempts INTEGER DEFAULT 1,
    status TEXT NOT NULL,
    degraded TEXT,
    request_json TEXT,
    response_text TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_calls_run ON model_calls(run_id, id);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    type TEXT NOT NULL,
    phase TEXT,
    status TEXT,
    message TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run_id ON run_events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON run_events(type);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    type TEXT NOT NULL,
    command TEXT,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'repo',
    record_type TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT,
    source_path TEXT,
    git_commit TEXT,
    file_hash TEXT,
    confidence REAL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'fresh',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_records(scope);
CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_records(status);

CREATE TABLE IF NOT EXISTS repo_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT,
    size INTEGER,
    file_hash TEXT,
    indexed_at TEXT NOT NULL,
    UNIQUE(repo_path, file_path)
);

CREATE TABLE IF NOT EXISTS repo_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    file_path TEXT NOT NULL,
    symbol_type TEXT NOT NULL,
    name TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    parent TEXT,
    indexed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbols_repo ON repo_symbols(repo_path);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    task_prompt TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    schedule_type TEXT NOT NULL DEFAULT 'one_shot',
    schedule_expr TEXT,
    timezone TEXT DEFAULT 'UTC',
    next_run_at TEXT,
    last_run_at TEXT,
    enabled INTEGER DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    runtime_mode TEXT DEFAULT 'local',
    model_profile TEXT,
    provider TEXT DEFAULT 'mock',
    model TEXT,
    memory_mode TEXT DEFAULT 'repo',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduled_task_id INTEGER NOT NULL REFERENCES scheduled_tasks(id),
    run_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    message TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    report_md TEXT,
    diff_patch TEXT,
    commands_log TEXT,
    approvals_log TEXT,
    events_jsonl TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_cache (
    cache_key TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    provider TEXT NOT NULL,
    results_json TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache(expires_at);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    calendar_id TEXT NOT NULL DEFAULT 'patchquest',
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    timezone TEXT DEFAULT 'UTC',
    location TEXT,
    source_provider TEXT DEFAULT 'local',
    metadata_json TEXT,
    patchquest_run_id TEXT,
    scheduled_task_id INTEGER,
    reminder_minutes INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_time ON calendar_events(start_at, end_at);
CREATE INDEX IF NOT EXISTS idx_calendar_events_task ON calendar_events(scheduled_task_id);
"""


def now_iso() -> str:
    return ledger.now_iso()


def insert_event(
    conn: sqlite3.Connection,
    run_id: str,
    event_type: str,
    phase: str | None = None,
    status: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    **attribution: Any,
) -> int:
    return ledger.append(conn, run_id, event_type, phase=phase, status=status, message=message,
                         payload=payload, **attribution)[0]

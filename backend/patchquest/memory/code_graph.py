"""Lightweight code graph stored in SQLite — nodes, edges, queries."""

from __future__ import annotations

import logging
from pathlib import Path

from patchquest.database import get_db, now_iso

logger = logging.getLogger(__name__)


def init_code_graph() -> None:
    with get_db() as conn:
        conn.executescript(CODE_GRAPH_SCHEMA)


CODE_GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    parser_source TEXT DEFAULT 'unknown',
    indexed_at TEXT NOT NULL,
    UNIQUE(repo_path, file_path, node_type, name, line_start)
);

CREATE INDEX IF NOT EXISTS idx_code_nodes_repo ON code_nodes(repo_path);
CREATE INDEX IF NOT EXISTS idx_code_nodes_name ON code_nodes(name);

CREATE TABLE IF NOT EXISTS code_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_name TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    target_file TEXT,
    target_name TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_code_edges_repo ON code_edges(repo_path);
CREATE INDEX IF NOT EXISTS idx_code_edges_target ON code_edges(target_name);
"""


def upsert_node(
    repo_path: str, node_type: str, name: str, file_path: str,
    line_start: int | None = None, line_end: int | None = None,
    parser_source: str = "unknown",
) -> int:
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO code_nodes
               (repo_path, node_type, name, file_path, line_start, line_end, parser_source, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (repo_path, node_type, name, file_path, line_start, line_end, parser_source, now),
        )
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        return row[0]


def add_edge(
    repo_path: str, source_file: str, source_name: str,
    edge_type: str, target_name: str, target_file: str | None = None,
) -> None:
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO code_edges
               (repo_path, source_file, source_name, edge_type, target_file, target_name, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (repo_path, source_file, source_name, edge_type, target_file, target_name, now),
        )


def index_file_symbols(repo_path: str, file_path: str, symbols: list[dict]) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM code_nodes WHERE repo_path = ? AND file_path = ?",
            (repo_path, file_path),
        )
        conn.execute(
            "DELETE FROM code_edges WHERE repo_path = ? AND source_file = ?",
            (repo_path, file_path),
        )

    for sym in symbols:
        sym_type = sym.get("type", "unknown")
        name = sym.get("name", "")
        if not name:
            continue

        parser_source = sym.get("parser_source", "unknown")
        upsert_node(
            repo_path, sym_type, name, file_path,
            sym.get("line_start"), sym.get("line_end"),
            parser_source,
        )

        if sym_type == "import":
            add_edge(repo_path, file_path, name, "imports", name)
        elif sym_type == "include":
            add_edge(repo_path, file_path, name, "includes", name)


def find_definitions(repo_path: str, name: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM code_nodes
               WHERE repo_path = ? AND name = ? AND node_type NOT IN ('import', 'include')
               ORDER BY file_path""",
            (repo_path, name),
        ).fetchall()
    return [dict(r) for r in rows]


def find_importers(repo_path: str, module_name: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT source_file FROM code_edges
               WHERE repo_path = ? AND edge_type = 'imports' AND target_name = ?""",
            (repo_path, module_name),
        ).fetchall()
    return [dict(r) for r in rows]


def find_test_files(repo_path: str, file_path: str) -> list[str]:
    stem = Path(file_path).stem
    candidates = [f"test_{stem}", f"{stem}_test", f"tests/test_{stem}"]
    results = []
    with get_db() as conn:
        for candidate in candidates:
            rows = conn.execute(
                "SELECT DISTINCT file_path FROM code_nodes WHERE repo_path = ? AND file_path LIKE ?",
                (repo_path, f"%{candidate}%"),
            ).fetchall()
            results.extend(r["file_path"] for r in rows)
    return list(set(results))


def get_most_connected(repo_path: str, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT target_name, COUNT(*) as ref_count
               FROM code_edges WHERE repo_path = ?
               GROUP BY target_name ORDER BY ref_count DESC LIMIT ?""",
            (repo_path, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_file_symbols(repo_path: str, file_path: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM code_nodes WHERE repo_path = ? AND file_path = ? ORDER BY line_start",
            (repo_path, file_path),
        ).fetchall()
    return [dict(r) for r in rows]


def get_graph_stats(repo_path: str) -> dict:
    with get_db() as conn:
        node_count = conn.execute(
            "SELECT COUNT(*) as c FROM code_nodes WHERE repo_path = ?", (repo_path,)
        ).fetchone()["c"]
        edge_count = conn.execute(
            "SELECT COUNT(*) as c FROM code_edges WHERE repo_path = ?", (repo_path,)
        ).fetchone()["c"]
        parser_stats = conn.execute(
            """SELECT parser_source, COUNT(*) as c FROM code_nodes
               WHERE repo_path = ? GROUP BY parser_source""",
            (repo_path,),
        ).fetchall()
    return {
        "nodes": node_count,
        "edges": edge_count,
        "parser_sources": {r["parser_source"]: r["c"] for r in parser_stats},
    }


def clear_repo(repo_path: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM code_nodes WHERE repo_path = ?", (repo_path,))
        conn.execute("DELETE FROM code_edges WHERE repo_path = ?", (repo_path,))

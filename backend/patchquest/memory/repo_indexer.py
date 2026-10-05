"""Repository file indexer - scans and indexes repo structure."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from patchquest.database import get_db, now_iso
from patchquest.memory.symbol_extractors import extract_symbols

IGNORED_DIRS = frozenset({
    ".git", "node_modules", "dist", "build", "target",
    ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".next", ".turbo",
    "coverage", ".cache", ".tox", "egg-info",
})

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".sh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".md": "markdown",
    ".css": "css",
    ".html": "html",
    ".sql": "sql",
}

MAX_FILE_SIZE = 1_000_000


SYMBOL_LANGUAGES = ("python", "typescript", "javascript", "rust", "c", "cpp")
# A file modified this recently could still be modified again within the filesystem's timestamp granularity without
# its (size, mtime) changing, so it is always re-hashed. (The same "racily clean" rule git's index uses.)
RACY_NS = 2_000_000_000


def index_repo(repo_path: str) -> dict:
    """Bring the repository index up to date and say how much work that took.

    Incremental: a file whose size and modification time are unchanged since it was indexed is not read at all; one that
    was touched but has the same content is not re-parsed; a changed file's symbols are replaced; a file that disappeared
    (or grew past the size limit) is removed. The result separates what was reused from what was reprocessed so the saving
    can be measured. ``files_indexed`` / ``symbols_indexed`` are the totals now in the index.
    """
    root = Path(repo_path)
    if not root.is_dir():
        return {"error": f"Not a directory: {repo_path}", "files_indexed": 0}

    started = time.perf_counter()
    now, now_ns = now_iso(), time.time_ns()
    new = changed = unchanged = touched = reprocessed_symbols = 0
    seen: set[str] = set()

    with get_db() as conn:
        known = {r["file_path"]: r for r in conn.execute(
            "SELECT file_path, size, mtime_ns, file_hash FROM repo_files WHERE repo_path = ?", (repo_path,))}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for filename in filenames:
                filepath = Path(dirpath) / filename
                rel_path = str(filepath.relative_to(root))
                try:
                    stat = filepath.stat()
                except OSError:
                    continue
                if stat.st_size > MAX_FILE_SIZE:
                    continue
                seen.add(rel_path)
                prev = known.get(rel_path)
                if prev and prev["size"] == stat.st_size and prev["mtime_ns"] == stat.st_mtime_ns and now_ns - stat.st_mtime_ns > RACY_NS:
                    unchanged += 1  # same size and timestamp as when it was indexed: not even read
                    continue
                language = LANGUAGE_EXTENSIONS.get(filepath.suffix.lower())
                file_hash = _hash_file(filepath)
                same_content = prev is not None and prev["file_hash"] == file_hash
                conn.execute(
                    """INSERT INTO repo_files (repo_path, file_path, language, size, file_hash, mtime_ns, indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(repo_path, file_path) DO UPDATE SET
                         language=excluded.language, size=excluded.size, file_hash=excluded.file_hash,
                         mtime_ns=excluded.mtime_ns, indexed_at=excluded.indexed_at""",
                    (repo_path, rel_path, language, stat.st_size, file_hash, stat.st_mtime_ns, now))
                if same_content:
                    touched += 1  # timestamp moved, content did not: nothing to re-parse
                    continue
                new, changed = (new + 1, changed) if prev is None else (new, changed + 1)
                conn.execute("DELETE FROM repo_symbols WHERE repo_path = ? AND file_path = ?", (repo_path, rel_path))
                if language in SYMBOL_LANGUAGES:
                    try:
                        symbols = extract_symbols(filepath.read_text(errors="replace"), language)
                    except (OSError, UnicodeDecodeError):
                        continue
                    for sym in symbols:
                        conn.execute(
                            """INSERT INTO repo_symbols
                               (repo_path, file_path, symbol_type, name, line_start, line_end, parent, indexed_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                            (repo_path, rel_path, sym["type"], sym["name"], sym.get("line_start"), sym.get("line_end"), sym.get("parent"), now))
                        reprocessed_symbols += 1

        removed = sorted(set(known) - seen)
        for rel_path in removed:
            conn.execute("DELETE FROM repo_symbols WHERE repo_path = ? AND file_path = ?", (repo_path, rel_path))
            conn.execute("DELETE FROM repo_files WHERE repo_path = ? AND file_path = ?", (repo_path, rel_path))
        total_symbols = conn.execute("SELECT COUNT(*) FROM repo_symbols WHERE repo_path = ?", (repo_path,)).fetchone()[0]

    return {"files_indexed": len(seen), "symbols_indexed": total_symbols, "error": None,
            "files_new": new, "files_changed": changed, "files_removed": len(removed), "files_unchanged": unchanged,
            "files_touched_only": touched, "files_reprocessed": new + changed, "symbols_reprocessed": reprocessed_symbols,
            "cache_hit_rate": round((unchanged + touched) / len(seen), 4) if seen else None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
    except (OSError, PermissionError):
        return ""
    return h.hexdigest()[:16]

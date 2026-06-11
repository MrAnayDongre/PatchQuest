"""Repository file indexer - scans and indexes repo structure."""

from __future__ import annotations

import hashlib
import os
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


def index_repo(repo_path: str) -> dict:
    root = Path(repo_path)
    if not root.is_dir():
        return {"error": f"Not a directory: {repo_path}", "files_indexed": 0}

    files_indexed = 0
    symbols_indexed = 0
    now = now_iso()

    with get_db() as conn:
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

                ext = filepath.suffix.lower()
                language = LANGUAGE_EXTENSIONS.get(ext)

                file_hash = _hash_file(filepath)

                conn.execute(
                    """INSERT INTO repo_files (repo_path, file_path, language, size, file_hash, indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(repo_path, file_path) DO UPDATE SET
                         language=excluded.language, size=excluded.size,
                         file_hash=excluded.file_hash, indexed_at=excluded.indexed_at""",
                    (repo_path, rel_path, language, stat.st_size, file_hash, now),
                )
                files_indexed += 1

                if language in ("python", "typescript", "javascript", "rust", "c", "cpp"):
                    try:
                        content = filepath.read_text(errors="replace")
                        symbols = extract_symbols(content, language)
                        for sym in symbols:
                            conn.execute(
                                """INSERT INTO repo_symbols
                                   (repo_path, file_path, symbol_type, name, line_start, line_end, parent, indexed_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (repo_path, rel_path, sym["type"], sym["name"],
                                 sym.get("line_start"), sym.get("line_end"), sym.get("parent"), now),
                            )
                            symbols_indexed += 1
                    except (OSError, UnicodeDecodeError):
                        pass

    return {"files_indexed": files_indexed, "symbols_indexed": symbols_indexed, "error": None}


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
    except (OSError, PermissionError):
        return ""
    return h.hexdigest()[:16]

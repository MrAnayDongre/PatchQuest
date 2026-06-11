"""Repo map retrieval."""

from __future__ import annotations

from patchquest.database import get_db


def get_repo_map(repo_path: str) -> dict:
    with get_db() as conn:
        files = conn.execute(
            "SELECT file_path, language, size FROM repo_files WHERE repo_path = ? ORDER BY file_path",
            (repo_path,),
        ).fetchall()

        symbols = conn.execute(
            "SELECT file_path, symbol_type, name, line_start FROM repo_symbols WHERE repo_path = ? ORDER BY file_path, line_start",
            (repo_path,),
        ).fetchall()

    file_list = [dict(f) for f in files]
    symbol_list = [dict(s) for s in symbols]

    return {
        "files": file_list,
        "symbols": symbol_list,
        "total_files": len(file_list),
        "total_symbols": len(symbol_list),
    }

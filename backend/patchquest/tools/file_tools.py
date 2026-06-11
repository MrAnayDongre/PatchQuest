"""Structured file read/write tools with safety checks."""

from __future__ import annotations

import os
from pathlib import Path

from patchquest.paths import check_path_traversal, is_path_safe


def read_file(path: str, repo_root: str, start: int | None = None, end: int | None = None) -> dict:
    if not check_path_traversal(path):
        return {"error": "Path traversal detected", "content": None}

    full_path = os.path.join(repo_root, path) if not os.path.isabs(path) else path

    if not is_path_safe(full_path, repo_root):
        return {"error": "Path outside allowed workspace", "content": None}

    try:
        with open(full_path, "r", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return {"error": f"File not found: {path}", "content": None}
    except PermissionError:
        return {"error": f"Permission denied: {path}", "content": None}

    if start is not None or end is not None:
        s = (start or 1) - 1
        e = end or len(lines)
        lines = lines[s:e]

    content = "".join(lines)
    if len(content) > 500_000:
        content = content[:500_000] + "\n... [truncated]"

    return {"content": content, "lines": len(lines), "error": None}


def list_files(path: str, repo_root: str) -> dict:
    if not check_path_traversal(path):
        return {"error": "Path traversal detected", "files": []}

    full_path = os.path.join(repo_root, path) if not os.path.isabs(path) else path

    if not is_path_safe(full_path, repo_root):
        return {"error": "Path outside allowed workspace", "files": []}

    try:
        entries = []
        for entry in sorted(Path(full_path).iterdir()):
            entries.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else None,
            })
        return {"files": entries[:500], "error": None}
    except Exception as e:
        return {"error": str(e), "files": []}


def create_file(path: str, content: str, repo_root: str) -> dict:
    if not check_path_traversal(path):
        return {"error": "Path traversal detected", "success": False}

    full_path = os.path.join(repo_root, path) if not os.path.isabs(path) else path

    if not is_path_safe(full_path, repo_root):
        return {"error": "Path outside allowed workspace", "success": False}

    from patchquest.tools.secret_guard import has_secrets
    if has_secrets(content):
        return {"error": "Content contains potential secrets - blocked", "success": False}

    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
        return {"success": True, "error": None}
    except Exception as e:
        return {"error": str(e), "success": False}

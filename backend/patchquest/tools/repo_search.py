"""Repository search using ripgrep or Python fallback."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


def search_repo(query: str, repo_path: str, glob: str | None = None, max_results: int = 50) -> list[dict]:
    if shutil.which("rg"):
        return _rg_search(query, repo_path, glob, max_results)
    return _python_search(query, repo_path, glob, max_results)


def _rg_search(query: str, repo_path: str, glob: str | None, max_results: int) -> list[dict]:
    cmd = ["rg", "--line-number", "--max-count", str(max_results), "--no-heading"]
    if glob:
        cmd.extend(["--glob", glob])
    cmd.append(query)
    cmd.append(repo_path)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        matches = []
        for line in result.stdout.split("\n")[:max_results]:
            if ":" in line:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({
                        "file": os.path.relpath(parts[0], repo_path),
                        "line": int(parts[1]) if parts[1].isdigit() else 0,
                        "content": parts[2][:200],
                    })
        return matches
    except (subprocess.TimeoutExpired, Exception):
        return _python_search(query, repo_path, glob, max_results)


def _python_search(query: str, repo_path: str, glob: str | None, max_results: int) -> list[dict]:
    from patchquest.memory.repo_indexer import IGNORED_DIRS

    matches = []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    root = Path(repo_path)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]

        for filename in filenames:
            if glob and not _matches_glob(filename, glob):
                continue

            filepath = Path(dirpath) / filename
            try:
                with open(filepath, "r", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if pattern.search(line):
                            matches.append({
                                "file": str(filepath.relative_to(root)),
                                "line": i,
                                "content": line.strip()[:200],
                            })
                            if len(matches) >= max_results:
                                return matches
            except (OSError, PermissionError):
                continue

    return matches


def _matches_glob(filename: str, glob: str) -> bool:
    glob_pattern = glob.replace("*", ".*").replace("?", ".")
    return bool(re.match(glob_pattern, filename))

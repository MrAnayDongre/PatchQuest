"""Workspace safety enforcement."""

from __future__ import annotations

import os

from patchquest.paths import FORBIDDEN_PREFIXES, is_inside_repo


def validate_workspace_access(path: str, repo_root: str, write: bool = False) -> tuple[bool, str]:
    expanded = os.path.expanduser(path)
    resolved = os.path.realpath(expanded)

    for forbidden in FORBIDDEN_PREFIXES:
        if resolved.startswith(forbidden):
            return False, f"Access to {forbidden} is forbidden"

    if path.endswith(".env") or "/.env" in path:
        return False, ".env files are not accessible by default"

    if not is_inside_repo(resolved, repo_root):
        action = "write to" if write else "read"
        return False, f"Cannot {action} paths outside the repository without approval"

    return True, ""

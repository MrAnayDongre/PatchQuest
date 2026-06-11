"""Path safety utilities."""

from __future__ import annotations

import os
from pathlib import Path

from patchquest.config import get_config

FORBIDDEN_PREFIXES = [
    os.path.expanduser("~/.ssh"),
    os.path.expanduser("~/.aws"),
    os.path.expanduser("~/.gcp"),
    os.path.expanduser("~/.azure"),
    os.path.expanduser("~/.config/gcloud"),
    os.path.expanduser("~/.config/gh"),
]


def is_path_safe(path: str, repo_root: str) -> bool:
    try:
        resolved = Path(path).resolve()
        repo_resolved = Path(repo_root).resolve()
    except (OSError, ValueError):
        return False

    resolved_str = str(resolved)
    for forbidden in FORBIDDEN_PREFIXES:
        if resolved_str.startswith(forbidden):
            return False

    if not resolved_str.startswith(str(repo_resolved)):
        config = get_config()
        if not config.safety.allow_outside_repo:
            return False

    return True


def check_path_traversal(path: str) -> bool:
    normalized = os.path.normpath(path)
    if ".." in normalized.split(os.sep):
        return False
    return True


def is_inside_repo(path: str, repo_root: str) -> bool:
    try:
        resolved = Path(path).resolve()
        repo_resolved = Path(repo_root).resolve()
        return str(resolved).startswith(str(repo_resolved))
    except (OSError, ValueError):
        return False

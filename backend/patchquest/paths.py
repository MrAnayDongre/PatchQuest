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

    for forbidden in FORBIDDEN_PREFIXES:
        if _is_within(resolved, Path(forbidden)):
            return False

    if not _is_within(resolved, repo_resolved):
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
        return _is_within(Path(path).resolve(), Path(repo_root).resolve())
    except (OSError, ValueError):
        return False


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or lives under it (component-wise, not a string prefix)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class UnsafePathError(ValueError):
    """Raised when a model- or user-supplied path escapes the workspace."""


# Directories a patch may never write into, regardless of workspace boundaries.
PROTECTED_DIRS = (".git",)


def resolve_in_repo(repo_root: str, rel_path: str, *, for_write: bool = False) -> Path:
    """Resolve ``rel_path`` inside ``repo_root`` or raise :class:`UnsafePathError`.

    Rejects absolute paths, ``..`` components, NUL bytes, paths whose real location
    (after following symlinks, including those in parent directories) leaves the repo,
    forbidden credential directories, and (for writes) ``.git`` internals.
    """
    if not rel_path or "\x00" in rel_path:
        raise UnsafePathError("empty or invalid path")
    if os.path.isabs(rel_path) or rel_path.startswith(("~", "\\")):
        raise UnsafePathError(f"absolute paths are not allowed: {rel_path}")
    parts = Path(rel_path).parts
    if ".." in parts:
        raise UnsafePathError(f"path traversal in: {rel_path}")
    if for_write and parts and parts[0] in PROTECTED_DIRS:
        raise UnsafePathError(f"writing to {parts[0]}/ is not allowed: {rel_path}")

    root = Path(repo_root).resolve()
    target = (root / rel_path).resolve()  # resolves symlinks, even dangling ones
    if not _is_within(target, root):
        raise UnsafePathError(f"path resolves outside the repository: {rel_path}")
    for forbidden in FORBIDDEN_PREFIXES:
        if _is_within(target, Path(forbidden)):
            raise UnsafePathError(f"access to {forbidden} is forbidden")
    return target

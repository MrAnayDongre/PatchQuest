"""Repository fingerprints and drift classification.

A checkpoint records what the repository looked like; resume and promotion compare that with what
it looks like now, so a human's edits made while a run was down are never silently overwritten.

Git is only ever *read*, and with the repository's own configuration neutralised: ``core.fsmonitor``
and hooks in an untrusted repo's ``.git/config`` would otherwise execute during ``git status``.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from patchquest.execution.executor import scrubbed_env
from patchquest.paths import UnsafePathError, resolve_in_repo

GIT_TIMEOUT_S = 15
_GIT_HARDENING = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.untrackedCache=false",
                  "-c", "protocol.allow=never", "-c", "core.pager=cat")


class Drift(StrEnum):
    NO_DRIFT = "NO_DRIFT"
    SAFE_DRIFT = "SAFE_DRIFT"  # the repo moved, but nothing this run wrote or depends on for writing
    CONFLICTING_DRIFT = "CONFLICTING_DRIFT"  # a file this run modifies changed underneath it
    UNKNOWN_DRIFT = "UNKNOWN_DRIFT"  # cannot tell (no comparable data); treated as unsafe


@dataclass(frozen=True)
class RepoFingerprint:
    head: str | None = None
    branch: str | None = None
    status_hash: str | None = None  # hash of ``git status`` (modified/untracked), None outside git
    is_git: bool = False
    files: dict[str, str | None] = field(default_factory=dict)  # rel path -> sha256 (None: absent)
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RepoFingerprint:
        return cls(head=raw.get("head"), branch=raw.get("branch"), status_hash=raw.get("status_hash"),
                   is_git=bool(raw.get("is_git")), files=dict(raw.get("files") or {}),
                   errors=tuple(raw.get("errors") or ()))


@dataclass(frozen=True)
class DriftReport:
    kind: Drift
    conflicting: tuple[str, ...] = ()  # touched files that changed
    stale_context: tuple[str, ...] = ()  # read-only inputs that changed
    reasons: tuple[str, ...] = ()


def _git(repo: str, *args: str) -> str | None:
    env = {**scrubbed_env(), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(["git", *_GIT_HARDENING, "-C", repo, *args], capture_output=True,
                              timeout=GIT_TIMEOUT_S, env=env, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None


def file_sha(repo: str, rel: str) -> str | None:
    """sha256 of a file inside ``repo`` (None when absent or outside the repo)."""
    try:
        target = resolve_in_repo(repo, rel)
    except UnsafePathError:
        return None
    return hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None


def compute(repo_path: str, watch: Iterable[str] = ()) -> RepoFingerprint:
    repo = str(Path(repo_path).resolve())
    files = {rel: file_sha(repo, rel) for rel in sorted(set(watch))}
    head = _git(repo, "rev-parse", "HEAD")
    if head is None:
        return RepoFingerprint(files=files)  # not a git repo (or git unavailable): file hashes only
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    errors = () if status is not None else ("git status failed",)
    return RepoFingerprint(
        head=head.strip(), branch=(branch or "").strip() or None, is_git=True, files=files, errors=errors,
        status_hash=hashlib.sha256(status.encode()).hexdigest() if status is not None else None)


def classify(old: RepoFingerprint, new: RepoFingerprint, touched: Iterable[str] = ()) -> DriftReport:
    """Compare two fingerprints. ``touched`` are the files this run writes; the rest of ``old.files``
    are inputs it merely read."""
    touched_set = set(touched)
    compared = set(old.files) & set(new.files)
    changed = sorted(rel for rel in compared if old.files[rel] != new.files[rel])
    conflicting = tuple(rel for rel in changed if rel in touched_set)
    stale = tuple(rel for rel in changed if rel not in touched_set)

    if conflicting:
        return DriftReport(Drift.CONFLICTING_DRIFT, conflicting, stale,
                           (f"modified since the checkpoint: {', '.join(conflicting)}",))
    if old.errors or new.errors or (old.is_git != new.is_git):
        return DriftReport(Drift.UNKNOWN_DRIFT, (), stale, ("repository state could not be compared",))
    if not old.is_git and not old.files:
        return DriftReport(Drift.UNKNOWN_DRIFT, (), (), ("no repository state was recorded for comparison",))

    moved = [name for name, a, b in (("HEAD", old.head, new.head), ("branch", old.branch, new.branch),
                                       ("working tree", old.status_hash, new.status_hash)) if a != b]
    if moved or stale:
        reasons = ((f"{', '.join(moved)} changed, but not files this run modifies",) if moved else ()) + (
            (f"input files changed: {', '.join(stale)}",) if stale else ())
        return DriftReport(Drift.SAFE_DRIFT, (), stale, reasons)
    return DriftReport(Drift.NO_DRIFT)

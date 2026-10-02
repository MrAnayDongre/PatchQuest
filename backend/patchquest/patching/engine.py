"""Atomic, verified patch application.

Every change is computed in memory first. Nothing is written unless *all* files apply
cleanly, pass path/secret checks, and satisfy their preconditions. Writes use temp-file +
rename and are rolled back from a snapshot if any step fails.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchquest.patching.edits import (
    Change,
    CreateFile,
    DeleteFile,
    EditError,
    SearchReplace,
    WriteFile,
    apply_search_replace,
)
from patchquest.patching.unified import (
    FilePatch,
    PatchApplyError,
    apply_hunks,
    parse_unified_diff,
)
from patchquest.paths import UnsafePathError, resolve_in_repo
from patchquest.tools.secret_guard import scan_text

MAX_FILE_BYTES = 2_000_000


def sha256_bytes(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


@dataclass
class FileResult:
    path: str
    action: str  # create | modify | delete
    added: int = 0
    removed: int = 0


@dataclass
class PatchResult:
    success: bool
    files: list[FileResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)
    diff: str = ""
    snapshot: dict[str, bytes | None] = field(default_factory=dict)

    @property
    def files_changed(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def error(self) -> str:
        return "; ".join(self.errors)


def changes_from_unified_diff(diff: str) -> list[FilePatch]:
    patches = parse_unified_diff(diff)
    if not patches:
        raise PatchApplyError("Could not parse diff")
    return patches


def _decode(raw: bytes, path: str) -> tuple[str, str]:
    if len(raw) > MAX_FILE_BYTES:
        raise PatchApplyError(f"{path} is too large to patch ({len(raw)} bytes)")
    if b"\x00" in raw[:8192]:
        raise PatchApplyError(f"{path} looks binary; refusing to patch")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchApplyError(f"{path} is not valid UTF-8") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline


def _added_text(old: str, new: str) -> str:
    """Only lines the patch introduces — pre-existing content (even secrets) is not blocked."""
    old_lines = set(old.split("\n"))
    return "\n".join(ln for ln in new.split("\n") if ln not in old_lines)


def apply_changes(
    repo_root: str,
    changes: list[Change | FilePatch],
    *,
    expected_hashes: dict[str, str | None] | None = None,
    dry_run: bool = False,
) -> PatchResult:
    """Apply ``changes`` to ``repo_root`` atomically.

    ``expected_hashes`` maps repo-relative path -> sha256 of the content the change was
    planned against (None means "must not exist"). A mismatch aborts the whole patch, which
    is how a promotion from a scratch workspace detects that the real file moved on.
    """
    result = PatchResult(success=False)
    root = Path(repo_root)
    originals: dict[str, bytes | None] = {}
    working: dict[str, str | None] = {}
    newlines: dict[str, str] = {}

    def load(rel: str) -> None:
        if rel in working:
            return
        target = resolve_in_repo(repo_root, rel, for_write=True)
        raw = target.read_bytes() if target.is_file() else None
        if target.exists() and not target.is_file():
            raise PatchApplyError(f"{rel} is not a regular file")
        originals[rel] = raw
        if raw is None:
            working[rel], newlines[rel] = None, "\n"
        else:
            working[rel], newlines[rel] = _decode(raw, rel)

    try:
        for change in changes:
            if isinstance(change, FilePatch):
                rel = change.path
                load(rel)
                text = working[rel]
                if change.is_delete:
                    if text is None:
                        raise PatchApplyError(f"cannot delete missing file {rel}")
                    working[rel] = None
                    continue
                if change.is_new and text is not None and text != "":
                    raise PatchApplyError(f"{rel} already exists; diff expects a new file")
                lines = [] if text is None else text.split("\n")
                had_trailing = text is not None and text.endswith("\n")
                if had_trailing and lines and lines[-1] == "":
                    lines.pop()
                new_lines = apply_hunks(lines, change.hunks, rel)
                no_nl = any(h.no_newline_at_eof for h in change.hunks)
                joined = "\n".join(new_lines)
                if new_lines and (had_trailing or text is None) and not no_nl:
                    joined += "\n"
                working[rel] = joined
            elif isinstance(change, SearchReplace):
                load(change.path)
                text = working[change.path]
                if text is None:
                    raise EditError(f"cannot edit missing file {change.path}")
                working[change.path] = apply_search_replace(text, change)
            elif isinstance(change, CreateFile):
                load(change.path)
                if working[change.path] not in (None, ""):
                    raise PatchApplyError(f"{change.path} already exists")
                working[change.path] = change.content
            elif isinstance(change, WriteFile):
                load(change.path)
                working[change.path] = change.content
            elif isinstance(change, DeleteFile):
                load(change.path)
                if working[change.path] is None:
                    raise PatchApplyError(f"cannot delete missing file {change.path}")
                working[change.path] = None
    except (PatchApplyError, EditError, UnsafePathError, OSError) as exc:
        result.errors.append(str(exc))
        return result

    # Preconditions against the content the change was planned on.
    for rel, expected in (expected_hashes or {}).items():
        actual = sha256_bytes(originals.get(rel)) if rel in originals else None
        if rel in originals and actual != expected:
            result.errors.append(f"precondition failed for {rel}: file changed since it was read")
    if result.errors:
        return result

    diff_parts: list[str] = []
    for rel, new_text in working.items():
        old_text = None if originals[rel] is None else _decode(originals[rel], rel)[0]
        if old_text == new_text:
            continue
        action = "create" if old_text is None else "delete" if new_text is None else "modify"
        old_l = (old_text or "").split("\n") if old_text else []
        new_l = (new_text or "").split("\n") if new_text else []
        udiff = list(difflib.unified_diff(
            old_l, new_l, f"a/{rel}" if old_text is not None else "/dev/null",
            f"b/{rel}" if new_text is not None else "/dev/null", lineterm="",
        ))
        added = sum(1 for ln in udiff if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in udiff if ln.startswith("-") and not ln.startswith("---"))
        result.files.append(FileResult(rel, action, added, removed))
        diff_parts.append("\n".join(udiff))
        if new_text:
            findings = scan_text(_added_text(old_text or "", new_text), rel)
            result.findings.extend(findings)
    result.diff = "\n".join(diff_parts) + ("\n" if diff_parts else "")

    if result.findings:
        result.errors.append("SecretGuard blocked this patch")
        result.files = []
        return result
    if not result.files:
        result.success = True  # nothing to do is not an error
        return result
    if dry_run:
        result.success = True
        return result

    result.snapshot = {f.path: originals[f.path] for f in result.files}
    try:
        for f in result.files:
            _write(root, f.path, working[f.path], newlines[f.path])
    except OSError as exc:
        rollback(repo_root, result.snapshot)
        result.errors.append(f"write failed, rolled back: {exc}")
        result.files = []
        return result
    result.success = True
    return result


def _write(root: Path, rel: str, text: str | None, newline: str) -> None:
    target = resolve_in_repo(str(root), rel, for_write=True)
    if text is None:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\n", newline).encode("utf-8")
    mode = target.stat().st_mode & 0o777 if target.exists() else None
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".pq-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def rollback(repo_root: str, snapshot: dict[str, bytes | None]) -> None:
    """Restore files captured in ``PatchResult.snapshot`` (None => file did not exist)."""
    for rel, raw in snapshot.items():
        target = resolve_in_repo(repo_root, rel, for_write=True)
        if raw is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

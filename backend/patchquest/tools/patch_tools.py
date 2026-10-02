"""Structured patch application with safety checks (compatibility wrappers).

The implementation lives in :mod:`patchquest.patching`; these helpers keep the original
result-dict API used by callers and tests.
"""

from __future__ import annotations

from typing import Any

from patchquest.patching import apply_changes, changes_from_unified_diff
from patchquest.patching.unified import PatchApplyError
from patchquest.paths import UnsafePathError, resolve_in_repo
from patchquest.tools.secret_guard import scan_text


def apply_unified_diff(diff: str, repo_root: str) -> dict[str, Any]:
    # Fail fast on secrets the patch would introduce. Only added lines count: a patch
    # that *removes* a leaked credential must not be blocked by it.
    added = "\n".join(
        ln[1:] for ln in diff.split("\n") if ln.startswith("+") and not ln.startswith("+++")
    )
    findings = scan_text(added)
    if findings:
        return {
            "success": False,
            "error": "SecretGuard blocked this patch",
            "findings": [{"type": f.finding_type, "preview": f.redacted_preview} for f in findings],
            "files_changed": [],
        }

    try:
        patches = changes_from_unified_diff(diff)
    except PatchApplyError as exc:
        return {"success": False, "error": str(exc), "files_changed": []}

    result = apply_changes(repo_root, patches)
    if not result.success:
        out: dict[str, Any] = {"success": False, "error": result.error, "files_changed": []}
        if result.findings:
            out["findings"] = [
                {"type": f.finding_type, "preview": f.redacted_preview} for f in result.findings
            ]
        return out
    return {"success": True, "files_changed": result.files_changed, "errors": [], "diff": result.diff}


def replace_range(path: str, start_line: int, end_line: int, new_text: str, repo_root: str) -> dict[str, Any]:
    try:
        full_path = resolve_in_repo(repo_root, path, for_write=True)
    except UnsafePathError as exc:
        msg = str(exc)
        return {"success": False, "error": f"Path traversal detected: {msg}" if ".." in msg else msg}

    if scan_text(new_text):
        return {"success": False, "error": "SecretGuard blocked: content contains secrets"}

    try:
        lines = full_path.read_text().splitlines(keepends=True)
        replacement = new_text.splitlines(keepends=True)
        if replacement and not replacement[-1].endswith("\n"):
            replacement[-1] += "\n"
        result_lines = lines[: start_line - 1] + replacement + lines[end_line:]
        full_path.write_text("".join(result_lines))
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}

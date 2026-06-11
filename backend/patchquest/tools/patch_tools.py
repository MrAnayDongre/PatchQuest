"""Structured patch application with safety checks."""

from __future__ import annotations

import os
import re
from typing import Any

from patchquest.paths import check_path_traversal, is_path_safe
from patchquest.tools.secret_guard import scan_text


def apply_unified_diff(diff: str, repo_root: str) -> dict[str, Any]:
    findings = scan_text(diff)
    if findings:
        return {
            "success": False,
            "error": "SecretGuard blocked this patch",
            "findings": [{"type": f.finding_type, "preview": f.redacted_preview} for f in findings],
            "files_changed": [],
        }

    file_patches = _parse_unified_diff(diff)
    if not file_patches:
        return {"success": False, "error": "Could not parse diff", "files_changed": []}

    changed_files: list[str] = []
    errors: list[str] = []

    for file_path, hunks in file_patches.items():
        if not hunks:
            errors.append(f"No hunks for: {file_path}")
            continue
        if not check_path_traversal(file_path):
            errors.append(f"Path traversal in: {file_path}")
            continue

        full_path = os.path.join(repo_root, file_path)
        if not is_path_safe(full_path, repo_root):
            errors.append(f"Path outside workspace: {file_path}")
            continue

        try:
            if os.path.exists(full_path):
                with open(full_path) as f:
                    lines = f.readlines()
            else:
                lines = []

            new_lines = _apply_hunks(lines, hunks)
            if new_lines == lines:
                continue
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.writelines(new_lines)
            changed_files.append(file_path)
        except Exception as e:
            errors.append(f"Failed to patch {file_path}: {e}")

    if errors and not changed_files:
        return {"success": False, "error": "; ".join(errors), "files_changed": []}

    return {"success": True, "files_changed": changed_files, "errors": errors}


def replace_range(path: str, start_line: int, end_line: int, new_text: str, repo_root: str) -> dict[str, Any]:
    if not check_path_traversal(path):
        return {"success": False, "error": "Path traversal detected"}

    full_path = os.path.join(repo_root, path) if not os.path.isabs(path) else path
    if not is_path_safe(full_path, repo_root):
        return {"success": False, "error": "Path outside workspace"}

    findings = scan_text(new_text)
    if findings:
        return {"success": False, "error": "SecretGuard blocked: content contains secrets"}

    try:
        with open(full_path) as f:
            lines = f.readlines()

        new_lines = new_text.split("\n")
        if new_text and not new_text.endswith("\n"):
            new_lines = [l + "\n" for l in new_lines[:-1]] + [new_lines[-1]]
        else:
            new_lines = [l + "\n" for l in new_lines if l or new_text.endswith("\n")]

        result_lines = lines[:start_line - 1] + new_lines + lines[end_line:]

        with open(full_path, "w") as f:
            f.writelines(result_lines)

        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _parse_unified_diff(diff: str) -> dict[str, list[dict]]:
    files: dict[str, list[dict]] = {}
    current_file: str | None = None
    current_hunk: dict | None = None

    for line in diff.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in files:
                files[current_file] = []
        elif line.startswith("@@"):
            match = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if match and current_file is not None:
                current_hunk = {
                    "old_start": int(match.group(1)),
                    "new_start": int(match.group(3)),
                    "lines": [],
                }
                files[current_file].append(current_hunk)
        elif current_hunk is not None:
            if line.startswith("+") or line.startswith("-") or line.startswith(" "):
                current_hunk["lines"].append(line)

    return files


def _apply_hunks(original_lines: list[str], hunks: list[dict]) -> list[str]:
    result = list(original_lines)
    offset = 0

    for hunk in hunks:
        old_start = hunk["old_start"] - 1 + offset
        removed = 0
        added_lines: list[str] = []

        for line in hunk["lines"]:
            if line.startswith("-"):
                removed += 1
            elif line.startswith("+"):
                added_lines.append(line[1:] + "\n")
            # context lines are skipped for offset tracking

        result[old_start:old_start + removed] = added_lines
        offset += len(added_lines) - removed

    return result

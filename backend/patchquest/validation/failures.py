"""Extract failing test identifiers from runner output and compare against a baseline.

The point is attribution: a failure that also occurs without the patch is not a regression
caused by it, and a *new* failure inside a suite that was already red must not be hidden by
the old one. Runner output is parsed best-effort; when nothing can be parsed for a failed
command we fall back to the command's exit code as a single opaque failure id.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = (
    re.compile(r"^(?:FAILED|ERROR)\s+(\S+::\S+?)(?:\s+-\s.*)?$", re.M),                 # pytest -rf summary
    re.compile(r"^(?:FAIL|ERROR):\s+(\S+)\s+\(([^)]+)\)", re.M),                          # unittest
    re.compile(r"^\s*--- FAIL:\s+(\S+)", re.M),                                           # go test
    re.compile(r"^test\s+(\S+)\s+\.\.\.\s+FAILED", re.M),                                  # cargo test
    re.compile(r"^\s*(?:✕|×)\s+(.+?)(?:\s+\(\d+\s*ms\))?$", re.M),                        # jest / vitest
)


def extract_failed_tests(text: str) -> set[str]:
    """Return identifiers of failed tests found in ``text`` (empty set when none are parseable)."""
    found: set[str] = set()
    for pattern in _PATTERNS:
        for match in pattern.finditer(text or ""):
            found.add(".".join(g for g in match.groups() if g).strip())
    return found


def _ids(result: dict[str, Any]) -> set[str]:
    ids = extract_failed_tests((result.get("stdout") or "") + "\n" + (result.get("stderr") or ""))
    if not ids and not result.get("success", True):
        ids = {f"<exit {result.get('returncode')}>"}
    return ids


def classify_failures(patched: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Per failing command: which failures are new (caused by the patch) vs pre-existing.

    Both lists hold command-result dicts with ``command``, ``success``, ``stdout``, ``stderr``.
    """
    base_by_cmd = {r["command"]: _ids(r) for r in baseline if not r.get("success", True)}
    out: dict[str, dict[str, list[str]]] = {}
    for result in patched:
        if result.get("success", True):
            continue
        ids = _ids(result)
        before = base_by_cmd.get(result["command"], set())
        out[result["command"]] = {"new": sorted(ids - before), "preexisting": sorted(ids & before)}
    return out

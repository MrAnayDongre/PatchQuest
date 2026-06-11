"""Context object passed through phases of a run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Scoped constraints — limit blast radius, not global read-only.
_SCOPE_ONLY_CONSTRAINTS = (
    "do not modify any other files",
    "don't modify any other files",
    "do not change any other files",
    "don't change any other files",
    "do not edit any other files",
    "don't edit any other files",
    "keep the change minimal",
    "only modify readme.md",
    "only modify readme",
    "do not change code files",
    "don't change code files",
)

# Explicit read-only signals that win even when mutation verbs appear elsewhere.
_READ_ONLY_OVERRIDES = (
    "without applying",
    "do not apply",
    "don't apply",
    "do not edit",
    "don't edit",
    "do not modify files",
    "don't modify files",
    "do not make code changes",
    "don't make code changes",
    "no code changes",
    "report only",
    "read-only",
    "read only",
)

_MUTATION_PHRASES = (
    "write to",
    "save the file",
    "add exactly this sentence",
    "modify readme",
    "update readme",
)

_MUTATION_RE = re.compile(
    r"\b("
    r"modify|update|add|insert|remove|delete|change|edit|replace|create|"
    r"implement|fix|refactor|rename|move|patch|write"
    r")\b",
    re.IGNORECASE,
)

_READ_ONLY_INTENT_RE = re.compile(
    r"\b("
    r"inspect|analyze|analyse|summarize|summarise|explain|describe|"
    r"overview|audit|review|list|architecture summary"
    r")\b",
    re.IGNORECASE,
)


def _scrub_scope_constraints(text: str) -> str:
    result = text
    for phrase in _SCOPE_ONLY_CONSTRAINTS:
        result = result.replace(phrase, " ")
    return result


def _has_mutation_intent(task_lower: str) -> bool:
    scrubbed = _scrub_scope_constraints(task_lower)
    for phrase in _MUTATION_PHRASES:
        if phrase in scrubbed:
            return True
    return bool(_MUTATION_RE.search(scrubbed))


def _has_read_only_override(task_lower: str) -> bool:
    return any(phrase in task_lower for phrase in _READ_ONLY_OVERRIDES)


def _has_read_only_intent(task_lower: str) -> bool:
    scrubbed = _scrub_scope_constraints(task_lower)
    return bool(_READ_ONLY_INTENT_RE.search(scrubbed))


def _detect_read_only(task: str, dry_run: bool) -> bool:
    """Return True when the task primary intent is inspect/analyze without edits."""
    if dry_run:
        return True

    task_lower = task.lower()

    if _has_read_only_override(task_lower):
        return True

    if _has_mutation_intent(task_lower):
        return False

    if _has_read_only_intent(task_lower):
        return True

    return False


def extract_quoted_sentence(task: str) -> str | None:
    """Extract a quoted sentence from a task, if present."""
    for pattern in (r'"([^"]{10,})"', r"'([^']{10,})'"):
        match = re.search(pattern, task)
        if match:
            return match.group(1)
    return None


@dataclass
class RunContext:
    run_id: str
    repo_path: str
    task: str
    provider: str = "mock"
    model: str | None = None
    runtime_mode: str = "local"
    dry_run: bool = False
    read_only: bool = False
    analysis: str | None = None
    plan: dict[str, Any] | None = None
    selected_files: list[str] = field(default_factory=list)
    selected_context: dict[str, str] = field(default_factory=dict)
    proposed_diff: str | None = None
    applied_files: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    commands_run: list[dict[str, Any]] = field(default_factory=list)
    security_findings: list[dict[str, Any]] = field(default_factory=list)
    secret_findings: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    memory_updates: list[dict[str, Any]] = field(default_factory=list)

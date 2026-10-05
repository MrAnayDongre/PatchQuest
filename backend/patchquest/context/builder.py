"""Deterministic context selection.

The model is never trusted to say what a file contains. Files are chosen by evidence
(named in the task, planned by the planner, matched by path/symbol), read from disk inside
the workspace boundary, redacted, and cut to a budget. Each item records why it was chosen,
so a run can answer "why did the agent see this file?".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from patchquest.paths import UnsafePathError, resolve_in_repo
from patchquest.runtime.workspace import is_secret_file
from patchquest.tools.secret_guard import redact_secrets

# Rough heuristic: 1 token ~ 4 characters. Good enough for budgeting, not for billing.
CHARS_PER_TOKEN = 4
STOPWORDS = frozenset(
    "the a an and or of to in on for with from by is are be this that it as at into add fix update make "
    "change new file files code only exactly sentence modify remove create use using should must not do "
    "dont any other one two line lines test tests please".split()
)

REASON_USER_REFERENCE = "user_reference"
REASON_PLANNED = "planner_requested"
REASON_PATH_MATCH = "path_match"
REASON_SYMBOL_MATCH = "symbol_match"
REASON_TEST_RELATION = "test_relation"
REASON_STACKTRACE = "stacktrace_match"


# Named selection strategies (``agent.context_strategy``). "lexical" is the long-standing behaviour. "focused" drops
# weakly evidenced candidates (below a quarter of the best score) and attaches tests only to the two best sources:
# on the evaluation fixture it keeps recall and removes about a fifth of the tokens (see docs/evaluation.md).
STRATEGIES: dict[str, dict[str, float | int | None]] = {
    "lexical": {},
    "focused": {"relative_cutoff": 0.25, "tests_for_top": 2},
}


@dataclass
class ContextItem:
    path: str
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    content: str = ""
    truncated: bool = False
    sha256: str = ""
    chars: int = 0

    def provenance(self) -> dict:
        d = asdict(self)
        d.pop("content")
        return d


def _keywords(task: str) -> list[str]:
    words: list[str] = []
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]*", task):
        for part in re.split(r"[_./-]+|(?<=[a-z])(?=[A-Z])", raw):
            part = part.lower()
            if len(part) >= 3 and part not in STOPWORDS:
                words.append(part)
    return list(dict.fromkeys(words))


def _referenced_paths(task: str) -> list[str]:
    return re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,6}", task)


def _read_text(repo_path: str, rel: str, limit: int) -> tuple[str, bool, str] | None:
    try:
        target = resolve_in_repo(repo_path, rel)
    except UnsafePathError:
        return None
    if not target.is_file() or is_secret_file(target.name):
        return None
    try:
        raw = target.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    text = raw.decode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).hexdigest()
    truncated = len(text) > limit
    return redact_secrets(text[:limit]), truncated, digest


def build_context(
    repo_path: str,
    task: str,
    repo_map: dict | None = None,
    planned_files: list[str] | None = None,
    *,
    failure_text: str = "",
    budget_tokens: int = 6000,
    per_file_chars: int = 8000,
    max_files: int = 8,
    relative_cutoff: float = 0.0,
    tests_for_top: int | None = None,
    strategy: str | None = None,
) -> list[ContextItem]:
    """Select and read the files most likely to matter for ``task`` within a token budget.

    ``relative_cutoff`` (0 = off) drops candidates whose score is below that fraction of the best score, unless the
    evidence is explicit (the user named the file, the planner asked for it, a traceback names it, or it is the test of
    a kept file). ``tests_for_top`` limits "tests that correspond to selected sources" to the top N sources.
    """
    if strategy is not None:
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown context strategy '{strategy}' (one of: {', '.join(STRATEGIES)})")
        relative_cutoff = float(STRATEGIES[strategy].get("relative_cutoff") or relative_cutoff)
        tests_for_top = STRATEGIES[strategy].get("tests_for_top", tests_for_top)  # type: ignore[assignment]
    repo_map = repo_map or {"files": [], "symbols": []}
    known = {f["file_path"] for f in repo_map.get("files", [])}
    scores: dict[str, ContextItem] = {}

    def bump(path: str, points: float, reason: str) -> None:
        item = scores.setdefault(path, ContextItem(path=path))
        item.score += points
        if reason not in item.reasons:
            item.reasons.append(reason)

    # 1. Files the user named explicitly (by path or basename).
    refs = _referenced_paths(task)
    for ref in refs:
        for path in known | {ref}:
            if path == ref or Path(path).name == Path(ref).name:
                if (Path(repo_path) / path).is_file():
                    bump(path, 200, REASON_USER_REFERENCE)

    # 2. Files the planner asked for (only if they really exist).
    for path in planned_files or []:
        if isinstance(path, str) and (Path(repo_path) / path).is_file():
            bump(path, 100, REASON_PLANNED)

    # 2b. Files named in a failing command's output (tracebacks, compiler errors).
    for ref in dict.fromkeys(_referenced_paths(failure_text)):
        for path in known | {ref}:
            if path == ref or ref.endswith("/" + path) or path.endswith("/" + ref):
                if (Path(repo_path) / path).is_file():
                    bump(path, 150, REASON_STACKTRACE)

    # 3. Path and symbol evidence from the repo index.
    words = _keywords(task)
    if words:
        by_file: dict[str, set[str]] = {}
        for sym in repo_map.get("symbols", []):
            by_file.setdefault(sym["file_path"], set()).add(sym["name"].lower())
        for path in known:
            path_tokens = set(re.split(r"[^a-z0-9]+", path.lower()))
            hits = [w for w in words if w in path_tokens]
            if hits:
                bump(path, 6 * len(hits), REASON_PATH_MATCH)
            sym_hits = [w for w in words if any(w in name for name in by_file.get(path, ()))]
            if sym_hits:
                bump(path, 4 * min(len(sym_hits), 5), REASON_SYMBOL_MATCH)

    # 4. Tests that correspond to selected sources.
    sources = sorted(scores, key=lambda p: (-scores[p].score, p))
    for path in sources[:tests_for_top] if tests_for_top is not None else sources:
        stem = Path(path).stem
        for cand in known:
            name = Path(cand).name
            if name in (f"test_{stem}.py", f"{stem}_test.py", f"{stem}.test.ts", f"{stem}.test.tsx", f"{stem}.spec.ts"):
                bump(cand, 8, REASON_TEST_RELATION)

    ranked = sorted(scores.values(), key=lambda i: (-i.score, i.path))
    if relative_cutoff > 0 and ranked:
        explicit = {REASON_USER_REFERENCE, REASON_PLANNED, REASON_STACKTRACE, REASON_TEST_RELATION}
        floor = ranked[0].score * relative_cutoff
        ranked = [i for i in ranked if i.score >= floor or explicit & set(i.reasons)]
    selected: list[ContextItem] = []
    remaining = budget_tokens * CHARS_PER_TOKEN
    for item in ranked:
        if len(selected) >= max_files or remaining <= 200:
            break
        loaded = _read_text(repo_path, item.path, min(per_file_chars, remaining))
        if loaded is None:
            continue
        item.content, item.truncated, item.sha256 = loaded
        item.chars = len(item.content)
        remaining -= item.chars
        selected.append(item)
    return selected


def render_context(items: list[ContextItem]) -> str:
    """Render items as clearly delimited, untrusted data for a prompt."""
    blocks = []
    for item in items:
        flag = " truncated=\"true\"" if item.truncated else ""
        blocks.append(
            f'<file path="{item.path}" sha256="{item.sha256[:12]}" reasons="{",".join(item.reasons)}"{flag}>\n'
            f"{item.content}\n</file>"
        )
    return "\n".join(blocks) if blocks else "(no files selected)"

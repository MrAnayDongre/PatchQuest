"""Evaluation task format and corpus loading.

A task is a small, self-contained repository plus a request, a *visible* test the agent can see and
a *hidden oracle* (extra tests added only after the run) so a patch that merely satisfies the visible
test is not counted as success. ``solution`` is a scripted reference answer used by ``scripted`` mode
to validate the engine and the corpus deterministically.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
BUNDLED_CORPUS = Path(__file__).parent / "corpus"


@dataclass(frozen=True)
class EvalTask:
    id: str
    category: str
    difficulty: str
    task: str
    files: dict[str, str]
    test_command: str
    oracle_command: str
    oracle_files: dict[str, str]
    forbid_changes: tuple[str, ...] = ()
    solution: dict[str, Any] = field(default_factory=dict)


def _parse(path: Path) -> EvalTask:
    raw = yaml.safe_load(path.read_text())
    if raw.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"{path.name}: unsupported task schema {raw.get('schema')!r} (expected {SCHEMA_VERSION})")
    oracle = raw["oracle"]
    return EvalTask(
        id=raw["id"], category=raw["category"], difficulty=raw.get("difficulty", "medium"), task=raw["task"],
        files=raw["files"], test_command=raw["test_command"], oracle_command=oracle["command"],
        oracle_files=oracle.get("files", {}), forbid_changes=tuple(oracle.get("forbid_changes", ())),
        solution=raw.get("solution", {}),
    )


def load_corpus(path: str | Path | None = None, only: str | None = None) -> list[EvalTask]:
    """Load every task in ``path`` (default: the bundled corpus), optionally filtered.

    ``only`` matches a task id, a category, or a comma-separated list of those.
    """
    root = Path(path) if path else BUNDLED_CORPUS
    tasks = [_parse(p) for p in sorted(root.glob("*.yaml"))]
    if not tasks:
        raise ValueError(f"no tasks found in {root}")
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task ids in corpus")
    if only:
        wanted = {w.strip() for w in only.split(",") if w.strip()}
        tasks = [t for t in tasks if t.id in wanted or t.category in wanted]
        if not tasks:
            raise ValueError(f"no tasks match {only!r}")
    return tasks


def corpus_digest(path: str | Path | None = None) -> str:
    """Stable hash of the corpus contents, so results are only compared against the same tasks."""
    root = Path(path) if path else BUNDLED_CORPUS
    h = hashlib.sha256()
    for p in sorted(root.glob("*.yaml")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]

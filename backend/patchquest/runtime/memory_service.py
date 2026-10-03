"""Using memory: writing with provenance, selecting what is worth showing a model, and keeping it honest.

Selection is deterministic and budgeted. A record is considered only if its tenant, scope and (for repository,
user and workflow scope) owner match the run; it is used only if it is active, fresh enough and confident
enough; it is shown only if it is relevant to this task; and when the same key exists at several scopes, only
the highest-precedence one is shown. Everything rejected is counted, so memory's cost and benefit can be measured.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchquest.domain import preferences as prefs
from patchquest.domain.memory import (
    MIN_EFFECTIVE_CONFIDENCE,
    Memory,
    MemoryKind,
    MemoryRefused,
    Source,
    Status,
    WriteOutcome,
    utcnow,
)
from patchquest.domain.policy import Scope
from patchquest.persistence import identity, memories
from patchquest.persistence.memories import Owner
from patchquest.runtime.repo_profile import FINGERPRINT_KEY, PREFIX

DEFAULT_BUDGET_TOKENS = 400
MIN_RELEVANCE = 0.15
_WORD = re.compile(r"[a-z][a-z0-9]{2,}")
_STOP = frozenset({"the", "and", "for", "with", "that", "this", "from", "not", "are", "use", "run", "tests", "test", "fix", "add"})


def owner_for(conn: sqlite3.Connection, workspace_id: str) -> Owner:
    row = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        raise LookupError(f"unknown workspace {workspace_id}")
    return Owner(str(row["org_id"]), workspace_id)


def scope_id_for(owner: Owner, scope: Scope, ref: str | None) -> str:
    """The id a scope's records are filed under: org and workspace are implied by the caller's tenant."""
    if scope is Scope.ORGANIZATION:
        return owner.org_id
    if scope is Scope.WORKSPACE:
        return owner.workspace_id
    if not ref:
        raise MemoryRefused(f"{scope.name.lower()} scope needs the {'repository path' if scope is Scope.REPOSITORY else 'id'} that owns it")
    return str(Path(ref).resolve()) if scope is Scope.REPOSITORY else ref


# --------------------------------------------------------------------------- writing
def remember(conn: sqlite3.Connection, owner: Owner, *, scope: Scope, ref: str | None, key: str, value: Any, kind: MemoryKind,
             source: Source, reason: str, actor: str, **kw: Any) -> tuple[WriteOutcome, Memory]:
    """Store a memory and audit it. ``MemoryRefused`` explains any refusal."""
    outcome, memory = memories.put(conn, owner, kind=kind, scope=scope, scope_id=scope_id_for(owner, scope, ref), key=key, value=value,
                                   source=source, reason=reason, authored_by=actor, **kw)
    identity.audit(conn, "memory.put", actor=actor, org_id=owner.org_id, workspace_id=owner.workspace_id,
                   target=f"{scope.name.lower()}:{key}", detail={"outcome": outcome.value, "source": source.value, "version": memory.version})
    return outcome, memory


def set_preference(conn: sqlite3.Connection, owner: Owner, *, scope: Scope, ref: str | None, key: str, value: Any, actor: str) -> Memory:
    """A person sets a preference at a scope. Validated against the known keys; always ``user_explicit``."""
    try:
        clean = prefs.validate(key, value)
    except prefs.PreferenceError as exc:
        raise MemoryRefused(f"{key}: {exc}") from None
    if scope not in prefs.PRECEDENCE:
        raise MemoryRefused(f"preferences cannot be set at {scope.name.lower()} scope")
    return remember(conn, owner, scope=scope, ref=ref, key=key, value=clean, kind=MemoryKind.PREFERENCE, source=Source.USER_EXPLICIT,
                    reason="set by a person", actor=actor)[1]


def clear_preference(conn: sqlite3.Connection, owner: Owner, *, scope: Scope, ref: str | None, key: str, actor: str) -> bool:
    current = memories.active(conn, owner, MemoryKind.PREFERENCE, scope, scope_id_for(owner, scope, ref), key)
    if current is None:
        return False
    memories.forget(conn, owner, current.id)
    identity.audit(conn, "memory.forget", actor=actor, org_id=owner.org_id, workspace_id=owner.workspace_id, target=f"{scope.name.lower()}:{key}")
    return True


def forget(conn: sqlite3.Connection, owner: Owner, memory_id: str, actor: str) -> bool:
    gone = memories.forget(conn, owner, memory_id)
    if gone is not None:
        identity.audit(conn, "memory.forget", actor=actor, org_id=owner.org_id, workspace_id=owner.workspace_id,
                       target=f"{gone.scope.name.lower()}:{gone.key}", detail={"memory_id": gone.id})
    return gone is not None


# --------------------------------------------------------------------------- preferences
def resolve_preferences(conn: sqlite3.Connection, owner: Owner, *, repo: str | None, user: str | None,
                        workflow: str | None = None) -> dict[str, prefs.Resolution]:
    memories.expire_due(conn, owner)
    visible = memories.visible(conn, owner, repo=repo, user=user, workflow=workflow, kinds=(MemoryKind.PREFERENCE,))
    return {key: prefs.resolve(visible, key) for key in prefs.PREFERENCES}


# --------------------------------------------------------------------------- invalidation
def revalidate(conn: sqlite3.Connection, owner: Owner, repo: str) -> list[Memory]:
    """Mark repository-scoped memories stale when a file they were learned from changed or disappeared.
    (Profile fields are recomputed by ``repo_profile.refresh`` instead.)"""
    root = Path(repo).resolve()
    stale = []
    for m in memories.visible(conn, owner, repo=repo):
        if m.scope is not Scope.REPOSITORY or not m.evidence or m.key.startswith(PREFIX):
            continue
        if _evidence_changed(root, m.evidence):
            memories.set_status(conn, m.id, Status.STALE)
            stale.append(m)
    return stale


def _evidence_changed(root: Path, evidence: dict[str, str] | Any) -> bool:
    from patchquest.runtime.repo_profile import _sha

    for rel, digest in evidence.items():
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file() or _sha(target) != digest:
            return True
    return False


# --------------------------------------------------------------------------- selection
@dataclass
class Selected:
    memory: Memory
    reason: str
    score: float
    tokens: int

    def to_dict(self) -> dict[str, Any]:
        m = self.memory
        return {"id": m.id, "scope": m.scope.name.lower(), "key": m.key, "source": m.source.value, "trusted": m.trusted,
                "reason": self.reason, "score": round(self.score, 3), "tokens": self.tokens}


@dataclass
class Selection:
    items: list[Selected] = field(default_factory=list)
    considered: int = 0
    stale_rejected: int = 0
    low_confidence_rejected: int = 0
    duplicates_avoided: int = 0
    not_relevant: int = 0
    over_budget: int = 0

    @property
    def tokens(self) -> int:
        return sum(i.tokens for i in self.items)

    def metrics(self) -> dict[str, int]:
        return {"considered": self.considered, "selected": len(self.items), "tokens": self.tokens, "stale_rejected": self.stale_rejected,
                "low_confidence_rejected": self.low_confidence_rejected, "duplicates_avoided": self.duplicates_avoided,
                "not_relevant": self.not_relevant, "over_budget": self.over_budget}

    def notes(self) -> list[dict[str, Any]]:
        """What goes into the run (and its checkpoint): enough to render a prompt block and to explain it."""
        return [{"id": i.memory.id, "text": render(i.memory), "source": i.memory.source.value, "trusted": i.memory.trusted,
                 "scope": i.memory.scope.name.lower(), "reason": i.reason} for i in self.items]


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def render(m: Memory) -> str:
    value = m.value if isinstance(m.value, str) else json.dumps(m.value, sort_keys=True)
    label = m.key[len(PREFIX):] if m.key.startswith(PREFIX) else m.key
    return f"{label}: {value}"[:400]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _relevance(m: Memory, task_words: set[str], paths: list[str]) -> tuple[float, str]:
    for prefix in m.metadata.get("applies_to", []) or []:
        if isinstance(prefix, str) and any(p.startswith(prefix) for p in paths):
            return 1.0, f"applies to {prefix}"
    words = _tokens(m.key.replace(".", " ").replace("_", " ") + " " + render(m))
    if not words:
        return 0.0, ""
    shared = sorted(words & task_words)
    return min(1.0, len(shared) / min(len(words), 6)), ("matches " + ", ".join(shared[:4])) if shared else ""


def select(conn: sqlite3.Connection, owner: Owner, *, repo: str | None, user: str | None, workflow: str | None, task: str,
           paths: list[str], budget_tokens: int = DEFAULT_BUDGET_TOKENS, include_user: bool = True) -> Selection:
    """Choose which notes to show a model for this task. Preferences are applied by code and are never shown as notes."""
    now = utcnow()
    memories.expire_due(conn, owner, now)
    sel = Selection()
    everything = memories.visible(conn, owner, repo=repo, user=user if include_user else None, workflow=workflow,
                                  kinds=(MemoryKind.REPOSITORY, MemoryKind.PROCEDURAL, MemoryKind.EPISODIC),
                                  statuses=(Status.ACTIVE, Status.STALE, Status.EXPIRED))
    task_words = _tokens(task) | _tokens(" ".join(paths))
    candidates: list[tuple[float, Memory, str]] = []
    for m in everything:
        if m.key == FINGERPRINT_KEY:
            continue
        sel.considered += 1
        relevance, why = _relevance(m, task_words, paths)
        is_profile = m.key.startswith(PREFIX)
        if m.status is not Status.ACTIVE:
            if relevance >= MIN_RELEVANCE or is_profile:
                sel.stale_rejected += 1
            continue
        if m.effective_confidence(now) < MIN_EFFECTIVE_CONFIDENCE:
            sel.low_confidence_rejected += 1
            continue
        if is_profile:
            relevance, why = max(relevance, MIN_RELEVANCE), why or "the repository's profile"
        elif relevance < MIN_RELEVANCE:
            sel.not_relevant += 1
            continue
        authority = {Source.USER_EXPLICIT: 1.0, Source.CONFIGURATION: 0.8, Source.REPOSITORY_DETECTED: 0.6}.get(m.source, 0.4)
        candidates.append((0.4 * relevance + 0.3 * authority + 0.3 * m.effective_confidence(now), m, why))

    best: dict[tuple[str, str], tuple[float, Memory, str]] = {}
    for score, m, why in sorted(candidates, key=lambda c: (c[1].scope, c[0])):  # narrower scope is processed later and wins
        k = (m.kind.value, m.key)
        if k in best:
            sel.duplicates_avoided += 1
        best[k] = (score, m, why)
    spent = 0
    for score, m, why in sorted(best.values(), key=lambda c: (-c[0], c[1].key)):
        cost = estimate_tokens(render(m))
        if spent + cost > budget_tokens:
            sel.over_budget += 1
            continue
        spent += cost
        sel.items.append(Selected(m, why, score, cost))
    return sel

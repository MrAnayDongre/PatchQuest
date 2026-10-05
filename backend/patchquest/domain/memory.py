"""What PatchQuest remembers, who may write it, and how long it is believed.

Four ideas are kept apart on purpose:

* **configuration** - how an installation is set up (``config.yaml``, environment);
* **policy** - what is allowed (``domain.policy``); it always dominates;
* **preference** - a person's or team's choice among allowed things (``domain.preferences``);
* **memory** - facts learned with provenance (this module): advisory, scoped, and able to go stale.

A memory is owned by exactly one *scope* (organisation, workspace, repository, user or workflow) inside one
tenant, and carries its source, the evidence it rests on, a confidence and a freshness. Where a record is
*allowed to come from* is a rule here, not a convention: only people and deterministic detection may create
anything that changes what PatchQuest does (preferences and procedures); an agent's own inference can only
leave low-authority notes, which are shown to models as unverified and never executed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from patchquest.domain.policy import Scope

SCHEMA_VERSION = 1
MAX_VALUE_BYTES = 4096
MAX_KEY = 120


class MemoryKind(StrEnum):
    REPOSITORY = "repository"  # facts about a codebase (its profile, conventions)
    PREFERENCE = "preference"  # a choice someone made (see domain.preferences)
    PROCEDURAL = "procedural"  # how to do something here (a command that works)
    EPISODIC = "episodic"  # what happened in earlier runs
    # Reserved so stored data and code can grow into them without a migration; writing them is refused for now.
    WORKING = "working"  # per-run scratch state lives in the run's checkpoint, not here
    TEAM = "team"  # needs a team entity


ACTIVE_KINDS = frozenset({MemoryKind.REPOSITORY, MemoryKind.PREFERENCE, MemoryKind.PROCEDURAL, MemoryKind.EPISODIC})
# Only entities that exist can own memory. There is no team or project yet, so there is no such scope.
ACTIVE_SCOPES = (Scope.ORGANIZATION, Scope.WORKSPACE, Scope.REPOSITORY, Scope.USER, Scope.WORKFLOW)


class Source(StrEnum):
    USER_EXPLICIT = "user_explicit"
    CONFIGURATION = "configuration"
    REPOSITORY_DETECTED = "repository_detected"
    TEST_RESULT = "test_result"
    CI = "ci"
    ACCEPTED_PATCH = "accepted_patch"
    REJECTED_PATCH = "rejected_patch"
    WORKFLOW_OBSERVATION = "workflow_observation"
    IMPORT = "import"
    AGENT_INFERENCE = "agent_inference"


# Authority orders sources: a write never replaces a record of higher authority.
AUTHORITY: dict[Source, int] = {
    Source.USER_EXPLICIT: 100, Source.CONFIGURATION: 80, Source.REPOSITORY_DETECTED: 60, Source.TEST_RESULT: 55, Source.CI: 50,
    Source.ACCEPTED_PATCH: 45, Source.REJECTED_PATCH: 45, Source.WORKFLOW_OBSERVATION: 30, Source.IMPORT: 20,
    Source.AGENT_INFERENCE: 10,
}
# What may be written by whom. Preferences and procedures change behaviour, so injected text cannot create them.
WRITABLE_BY: dict[MemoryKind, frozenset[Source]] = {
    MemoryKind.PREFERENCE: frozenset({Source.USER_EXPLICIT, Source.CONFIGURATION}),
    MemoryKind.PROCEDURAL: frozenset({Source.USER_EXPLICIT, Source.CONFIGURATION, Source.REPOSITORY_DETECTED, Source.TEST_RESULT}),
    MemoryKind.REPOSITORY: frozenset(Source),
    MemoryKind.EPISODIC: frozenset(Source),
}
TRUSTED_SOURCES = frozenset({Source.USER_EXPLICIT, Source.CONFIGURATION, Source.REPOSITORY_DETECTED, Source.TEST_RESULT, Source.CI})

# How long a record is believed without being re-verified; None: until its evidence changes or someone removes it.
DEFAULT_TTL: dict[Source, timedelta | None] = {
    Source.USER_EXPLICIT: None, Source.CONFIGURATION: None, Source.REPOSITORY_DETECTED: None, Source.TEST_RESULT: timedelta(days=30),
    Source.CI: timedelta(days=30), Source.ACCEPTED_PATCH: timedelta(days=90), Source.REJECTED_PATCH: timedelta(days=90),
    Source.WORKFLOW_OBSERVATION: timedelta(days=30), Source.IMPORT: timedelta(days=30), Source.AGENT_INFERENCE: timedelta(days=14),
}
DEFAULT_CONFIDENCE: dict[Source, float] = {
    Source.USER_EXPLICIT: 1.0, Source.CONFIGURATION: 0.95, Source.REPOSITORY_DETECTED: 0.8, Source.TEST_RESULT: 0.85, Source.CI: 0.8,
    Source.ACCEPTED_PATCH: 0.6, Source.REJECTED_PATCH: 0.6, Source.WORKFLOW_OBSERVATION: 0.5, Source.IMPORT: 0.4,
    Source.AGENT_INFERENCE: 0.3,
}
MIN_EFFECTIVE_CONFIDENCE = 0.25  # below this a record is not used, only listed


class Status(StrEnum):
    ACTIVE = "active"
    STALE = "stale"  # the evidence it rested on changed; kept for explanation, never used
    EXPIRED = "expired"  # past its time to live without being re-verified
    SUPERSEDED = "superseded"  # replaced by a newer version
    FORGOTTEN = "forgotten"  # removed on purpose; the row stays so the removal is visible in history


class MemoryRefused(ValueError):
    """A write was refused. The message is safe to show."""


class WriteOutcome(StrEnum):
    CREATED = "created"
    REINFORCED = "reinforced"  # same value seen again: confidence and verification time move, version does not
    UPDATED = "updated"  # new value from equal or higher authority: new version
    KEPT_EXISTING = "kept_existing"  # a lower-authority source lost to the existing record


@dataclass(frozen=True)
class Memory:
    id: str
    kind: MemoryKind
    scope: Scope
    scope_id: str
    key: str
    value: Any
    source: Source
    reason: str  # why it was stored
    authored_by: str  # "user:<id>", "runtime", "detector:repo_profile", ...
    confidence: float
    status: Status
    version: int
    created_at: datetime
    updated_at: datetime
    last_verified_at: datetime
    expires_at: datetime | None = None
    evidence: Mapping[str, str] = field(default_factory=dict)  # path -> sha256 of what it was learned from
    metadata: Mapping[str, Any] = field(default_factory=dict)
    org_id: str | None = None
    workspace_id: str | None = None
    supersedes: str | None = None

    @property
    def trusted(self) -> bool:
        return self.source in TRUSTED_SOURCES

    def freshness(self, now: datetime) -> float:
        """1.0 when just verified, falling to 0.5 at the end of its time to live; 0 once expired or not active."""
        if self.status is not Status.ACTIVE or (self.expires_at is not None and self.expires_at <= now):
            return 0.0
        ttl = DEFAULT_TTL[self.source]
        if ttl is None or self.expires_at is None:
            return 1.0
        age = (now - self.last_verified_at).total_seconds() / max(ttl.total_seconds(), 1.0)
        return max(0.5, 1.0 - 0.5 * min(age, 1.0))

    def effective_confidence(self, now: datetime) -> float:
        return round(self.confidence * self.freshness(now), 4)


_INSTRUCTION_LIKE = re.compile(
    r"(ignore|disregard|override|forget)\s+(all\s+|any\s+|the\s+|previous\s+|prior\s+|above\s+|these\s+)*(instructions?|polic(y|ies)|rules?|guard\w*)"
    r"|(disable|skip|turn off|bypass)\s+(all\s+|the\s+|any\s+)?(tests?|checks?|approvals?|validation|polic(y|ies)|safety)"
    r"|(send|upload|post|exfiltrate|leak)\s+.{0,40}(code|source|secrets?|credentials?|tokens?|files?)\s+.{0,30}(to|at)\s+\S+"
    r"|(always|never)\s+(run|approve|execute|trust)\b.{0,40}\b(without|no)\s+(ask|approval|review)", re.I | re.S)


def looks_like_instructions(text: str) -> bool:
    """Heuristic screen for text that tries to give orders to the agent. Defence in depth only: the real
    protection is that untrusted sources cannot create preferences or procedures at all."""
    return bool(_INSTRUCTION_LIKE.search(text))


def check_write(kind: MemoryKind, scope: Scope, source: Source, key: str, value_text: str) -> None:
    """Raise ``MemoryRefused`` unless this (kind, scope, source, content) may be stored."""
    if kind not in ACTIVE_KINDS:
        raise MemoryRefused(f"{kind.value} memory is not available yet")
    if scope not in ACTIVE_SCOPES:
        raise MemoryRefused(f"{scope.name.lower()} scope is not available (no such entity yet)")
    if source not in WRITABLE_BY[kind]:
        raise MemoryRefused(f"a {kind.value} record cannot come from {source.value}; only "
                           + ", ".join(sorted(s.value for s in WRITABLE_BY[kind])))
    if not key or len(key) > MAX_KEY or not re.fullmatch(r"[A-Za-z0-9_.:/\-]+", key):
        raise MemoryRefused("key must be 1-120 characters of letters, digits and _ . : / -")
    if len(value_text.encode()) > MAX_VALUE_BYTES:
        raise MemoryRefused(f"value is larger than {MAX_VALUE_BYTES} bytes")
    if source not in (Source.USER_EXPLICIT, Source.CONFIGURATION) and looks_like_instructions(value_text):
        raise MemoryRefused("this reads like an instruction to the agent, not a fact; only a person can store that")


def expiry(source: Source, now: datetime, ttl: timedelta | None = None) -> datetime | None:
    window = ttl if ttl is not None else DEFAULT_TTL[source]
    return None if window is None else now + window


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_public(m: Memory) -> dict[str, Any]:
    """The shape shown by APIs and the CLI: provenance included, internals not."""
    return {"id": m.id, "kind": m.kind.value, "scope": m.scope.name.lower(), "scope_id": m.scope_id, "key": m.key, "value": m.value,
            "source": m.source.value, "trusted": m.trusted, "reason": m.reason, "authored_by": m.authored_by,
            "confidence": m.confidence, "status": m.status.value, "version": m.version, "created_at": m.created_at.isoformat(),
            "updated_at": m.updated_at.isoformat(), "last_verified_at": m.last_verified_at.isoformat(),
            "expires_at": m.expires_at.isoformat() if m.expires_at else None, "evidence": dict(m.evidence), "metadata": dict(m.metadata)}

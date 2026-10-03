"""What a running run does with memory: adapt to the repository, pick notes, apply preferences, remember the outcome.

The state machine calls these at fixed points; each returns plain data (and the events to emit) so the machine
stays in charge of the ledger. Nothing here can loosen policy: preferences are read after policy has spoken,
and notes are advisory text for a model, never commands.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from patchquest.database import get_db
from patchquest.domain import preferences as prefs
from patchquest.domain.memory import MemoryKind, MemoryRefused, Source
from patchquest.domain.policy import Policy, Result, Scope
from patchquest.persistence.memories import Owner
from patchquest.providers.failover import LOCAL_PROVIDERS
from patchquest.runtime import memory_service as svc
from patchquest.runtime import policy as policy_runtime
from patchquest.runtime import repo_profile

USES_MEMORY = frozenset({"repo", "user"})


@dataclass
class RunScope:
    owner: Owner
    repo: str
    user: str | None
    memory_mode: str

    @classmethod
    def load(cls, conn: sqlite3.Connection, run_id: str) -> RunScope | None:
        row = conn.execute("SELECT workspace_id, created_by, repo_path, memory_mode FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return cls(svc.owner_for(conn, row["workspace_id"]), row["repo_path"], row["created_by"], row["memory_mode"] or "repo")


@dataclass
class Begin:
    notes: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # (type, message, payload)


def begin(run_id: str, task: str, paths: list[str], provider: str, chain: list[Policy]) -> Begin:
    """At repo-scan time: refresh the repository profile, drop what went stale, choose notes for the planner."""
    out = Begin()
    with get_db() as conn:
        scope = RunScope.load(conn, run_id)
        if scope is None or scope.memory_mode not in USES_MEMORY:
            return out
        change = repo_profile.refresh(conn, scope.owner, scope.repo)
        if change.any_change:
            out.events.append(("repository_profile_changed", "Repository profile updated: " + ", ".join(change.changed + change.removed),
                               change.to_dict()))
        stale = svc.revalidate(conn, scope.owner, scope.repo)
        if stale:
            out.events.append(("memory_invalidated", f"{len(stale)} remembered fact(s) no longer match the repository",
                               {"ids": [m.id for m in stale], "keys": [m.key for m in stale][:20]}))

        locality = "local" if provider in LOCAL_PROVIDERS else "cloud"
        verdict = policy_runtime.decide(chain, f"memory.inject.{locality}")
        if verdict.result is Result.DENY:
            out.events.append(("memory_withheld", f"Policy '{verdict.source_policy}' keeps memory out of {locality} model calls",
                               {"policy": verdict.to_dict()}))
            return out
        selection = svc.select(conn, scope.owner, repo=scope.repo, user=scope.user, workflow=None, task=task, paths=paths,
                               include_user=scope.memory_mode == "user")
        out.notes = selection.notes()
        out.events.append(("memory_selected", f"{len(selection.items)} of {selection.considered} remembered fact(s) selected",
                           {**selection.metrics(), "items": [i.to_dict() for i in selection.items]}))
    return out


def preferences(run_id: str) -> dict[str, prefs.Resolution]:
    with get_db() as conn:
        scope = RunScope.load(conn, run_id)
        if scope is None:
            return {}
        return svc.resolve_preferences(conn, scope.owner, repo=scope.repo, user=scope.user)


def record_outcome(run_id: str, task: str, outcome: str | None, verdict: str | None, files: list[str]) -> None:
    """After a run: leave a low-authority note of what happened, for later runs on the same repository."""
    with get_db() as conn:
        scope = RunScope.load(conn, run_id)
        if scope is None or scope.memory_mode not in USES_MEMORY or outcome not in ("applied", "rejected", "conflict"):
            return
        source = Source.ACCEPTED_PATCH if outcome == "applied" else Source.REJECTED_PATCH
        try:
            svc.remember(conn, scope.owner, scope=Scope.REPOSITORY, ref=scope.repo, key=f"run:{run_id[:12]}", kind=MemoryKind.EPISODIC,
                         value={"task": task[:200], "outcome": outcome, "verdict": verdict, "files": files[:10]}, source=source,
                         reason="what happened in an earlier run", actor="runtime", metadata={"applies_to": files[:10]})
        except MemoryRefused:
            return  # an unremarkable refusal (for example, secret-looking task text) must never fail a run


def preference_for(workspace_id: str, user: str | None, key: str) -> prefs.Resolution:
    """One preference as a workflow would see it (no repository: workflow steps are not tied to one)."""
    with get_db() as conn:
        owner = svc.owner_for(conn, workspace_id)
        return svc.resolve_preferences(conn, owner, repo=None, user=user)[key]

"""Preferences: a person's or team's choices among allowed things.

A preference never grants anything. It can pick between options policy permits, or ask PatchQuest to be *more*
careful; where it asks for less (``automation.external_writes: auto``) policy still decides and the run says so.
Only keys listed here exist, each with a validator and the code that applies it, so a stored preference can
never be a free-form instruction. Add a key only together with the code that reads it.

Precedence, lowest to highest: organisation < workspace < repository < user < workflow. System defaults sit
below all of them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from patchquest.domain.memory import Memory, Status, utcnow
from patchquest.domain.policy import Scope

PRECEDENCE = (Scope.ORGANIZATION, Scope.WORKSPACE, Scope.REPOSITORY, Scope.USER, Scope.WORKFLOW)


class PreferenceError(ValueError):
    pass


def _command_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not 1 <= len(value) <= 5 or not all(isinstance(c, str) and 0 < len(c) <= 200 for c in value):
        raise PreferenceError("expected 1-5 commands, each up to 200 characters")
    return [c.strip() for c in value]


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise PreferenceError("expected true or false")
    return value


def _auto_or_ask(value: Any) -> str:
    if value not in ("auto", "ask"):
        raise PreferenceError("expected 'auto' or 'ask'")
    return str(value)


@dataclass(frozen=True)
class PreferenceSpec:
    key: str
    description: str
    applied_by: str  # where in the code the value takes effect
    validate: Callable[[Any], Any]


PREFERENCES: dict[str, PreferenceSpec] = {s.key: s for s in (
    PreferenceSpec("test.commands", "Commands to validate a change with, tried before auto-detection (they must still be ones "
                   "the command policy runs unattended).", "state_machine._pick_test_commands", _command_list),
    PreferenceSpec("approval.ask_before_workspace_writes", "Ask before any command that writes in the workspace, "
                   "even those the command gate would run unattended.", "state_machine._exec", _boolean),
    PreferenceSpec("automation.external_writes", "'auto' asks PatchQuest to act on external systems without asking. "
                   "Policy still decides; the run records why approval was still required.", "workflows.engine._action", _auto_or_ask),
)}


def validate(key: str, value: Any) -> Any:
    spec = PREFERENCES.get(key)
    if spec is None:
        raise PreferenceError(f"unknown preference '{key}' (known: {', '.join(sorted(PREFERENCES))})")
    return spec.validate(value)


@dataclass(frozen=True)
class Resolution:
    key: str
    value: Any
    winner: Memory | None  # None: the system default applies
    overridden: tuple[Memory, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        def brief(m: Memory) -> dict[str, Any]:
            return {"scope": m.scope.name.lower(), "scope_id": m.scope_id, "value": m.value, "source": m.source.value, "memory_id": m.id}
        return {"key": self.key, "value": self.value, "decided_by": brief(self.winner) if self.winner else "system default",
                "overridden": [brief(m) for m in self.overridden]}


def resolve(records: list[Memory], key: str, default: Any = None) -> Resolution:
    """The value of ``key`` after layering every usable record by scope. Stale, expired or low-confidence records are ignored."""
    now = utcnow()
    usable = [m for m in records if m.key == key and m.status is Status.ACTIVE and m.freshness(now) > 0]
    usable.sort(key=lambda m: PRECEDENCE.index(m.scope) if m.scope in PRECEDENCE else -1, reverse=True)
    if not usable:
        return Resolution(key, default, None)
    return Resolution(key, usable[0].value, usable[0], tuple(usable[1:]))

"""The typed, JSON-serialisable action space.

An action is a plain dict ``{"type": <name>, **fields}`` so any policy (LLM, script, RL library) can emit it
without importing this package. :func:`parse_action` is the single validation point: it either returns a frozen
dataclass or raises :class:`InvalidAction`, which the environment turns into an observation, never a crash.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MAX_PATH_CHARS = 1024
MAX_TEXT_CHARS = 100_000


class InvalidAction(ValueError):
    """The action is not a well-formed member of the action space."""


@dataclass(frozen=True)
class ReadFile:
    path: str


@dataclass(frozen=True)
class ListDir:
    path: str


@dataclass(frozen=True)
class Search:
    query: str


@dataclass(frozen=True)
class Edit:
    path: str
    search: str
    replace: str


@dataclass(frozen=True)
class CreateFile:
    path: str
    content: str


@dataclass(frozen=True)
class RunTests:
    pass


@dataclass(frozen=True)
class Finish:
    pass


Action = ReadFile | ListDir | Search | Edit | CreateFile | RunTests | Finish

_ACTION_CLASSES = (ReadFile, ListDir, Search, Edit, CreateFile, RunTests, Finish)
ACTION_TYPES: dict[str, type] = {
    "read_file": ReadFile, "list_dir": ListDir, "search": Search, "edit": Edit,
    "create_file": CreateFile, "run_tests": RunTests, "finish": Finish,
}
_NAMES = {cls: name for name, cls in ACTION_TYPES.items()}
# Plain-dict schema (field -> type name) so adapters can build real gymnasium spaces from it.
ACTION_SCHEMAS: dict[str, dict[str, str]] = {
    "read_file": {"path": "str"}, "list_dir": {"path": "str"}, "search": {"query": "str"},
    "edit": {"path": "str", "search": "str", "replace": "str"}, "create_file": {"path": "str", "content": "str"},
    "run_tests": {}, "finish": {},
}
_PATH_FIELDS = {"path"}


def action_type(action: Action) -> str:
    return _NAMES[type(action)]


def action_to_dict(action: Action) -> dict[str, Any]:
    return {"type": action_type(action), **asdict(action)}


def parse_action(raw: Any) -> Action:
    """Validate ``raw`` (a dict or an :data:`Action`) strictly: known type, exact fields, string values, bounded size."""
    if isinstance(raw, _ACTION_CLASSES):
        raw = action_to_dict(raw)
    if not isinstance(raw, dict):
        raise InvalidAction(f"action must be an object, got {type(raw).__name__}")
    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in ACTION_TYPES:
        raise InvalidAction(f"unknown action type {kind!r}; expected one of {sorted(ACTION_TYPES)}")
    fields = {k: v for k, v in raw.items() if k != "type"}
    expected = ACTION_SCHEMAS[kind]
    if set(fields) != set(expected):
        raise InvalidAction(f"{kind} takes exactly {sorted(expected)}, got {sorted(fields)}")
    for name, value in fields.items():
        if not isinstance(value, str):
            raise InvalidAction(f"{kind}.{name} must be a string")
        limit = MAX_PATH_CHARS if name in _PATH_FIELDS else MAX_TEXT_CHARS
        if len(value) > limit:
            raise InvalidAction(f"{kind}.{name} is longer than {limit} characters")
    return ACTION_TYPES[kind](**fields)

"""Trajectory recording, JSONL export/import and dataset construction.

File layout (``schema_version`` 1): line 1 is a ``header`` object (schema_version, task_id, seed, environment
fingerprint, outcome); every following line is one ``step`` object. Exports are always secret-redacted; file
contents can additionally be dropped. A loader refuses files written by a newer schema, because silently
misreading a changed layout would corrupt training data.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchquest.rl.actions import InvalidAction, action_to_dict, parse_action
from patchquest.tools.secret_guard import redact_secrets

SCHEMA_VERSION = 1
DATASET_KINDS = ("successful", "failed", "paired")
# Actions/results that carry file text, which ``drop_file_contents`` removes.
_ACTION_TEXT_FIELDS = {"create_file": ("content",), "edit": ("search", "replace")}
_RESULT_TEXT_ACTIONS = frozenset({"read_file", "search", "edit", "create_file"})


class TrajectoryFormatError(ValueError):
    """The file is not a well-formed trajectory."""


class TrajectoryVersionError(TrajectoryFormatError):
    """The file was written by a newer schema than this code understands."""


@dataclass
class Trajectory:
    task_id: str
    seed: int
    fingerprint: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return bool(self.outcome.get("success"))

    @property
    def total_reward(self) -> float:
        return float(self.outcome.get("total_reward", 0.0))

    @property
    def trajectory_id(self) -> str:
        """Content hash of what the agent saw and did (not timings), stable across processes."""
        core = [self.task_id, self.seed, [[s["observation"], s["action"]] for s in self.steps]]
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]

    def to_jsonl(self, *, drop_file_contents: bool = False) -> str:
        safe = redact(self, drop_file_contents=drop_file_contents)
        header = {"kind": "header", "schema_version": SCHEMA_VERSION, "task_id": safe.task_id, "seed": safe.seed,
                  "fingerprint": safe.fingerprint, "outcome": safe.outcome}
        lines = [header, *({"kind": "step", **s} for s in safe.steps)]
        return "\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n"

    def dump(self, path: str | Path, *, drop_file_contents: bool = False) -> None:
        Path(path).write_text(self.to_jsonl(drop_file_contents=drop_file_contents), encoding="utf-8")


class TrajectoryRecorder:
    """Accumulates one episode: per step the observation the agent saw, its action, the result and the reward."""

    def __init__(self, task_id: str, seed: int, fingerprint: dict[str, Any]) -> None:
        self._traj = Trajectory(task_id, seed, fingerprint)
        self._final: dict[str, Any] | None = None

    def record(self, *, observation: dict[str, Any], action: Any, next_observation: dict[str, Any], terminated: bool,
               truncated: bool, info: dict[str, Any], wall_s: float) -> None:
        try:
            action = action_to_dict(parse_action(action))
        except InvalidAction:
            action = json.loads(json.dumps(action, default=repr))  # keep malformed actions verbatim for analysis
        self._traj.steps.append({
            "index": len(self._traj.steps), "observation": observation, "action": action,
            "result": next_observation["last_action"], "reward": info["reward_breakdown"].to_dict(),
            "terminated": terminated, "truncated": truncated, "wall_s": round(wall_s, 4),
            "counters": dict(info["counters"]),
        })
        if terminated or truncated:
            self._final = info

    def finish(self) -> Trajectory:
        info = self._final or {}
        self._traj.outcome = {
            "success": bool(info.get("success", False)), "ended": self._final is not None,
            "total_reward": round(sum(s["reward"]["total"] for s in self._traj.steps), 6),
            "steps": len(self._traj.steps), "terminated": bool(self._traj.steps and self._traj.steps[-1]["terminated"]),
            "truncated": bool(self._traj.steps and self._traj.steps[-1]["truncated"]),
        }
        return self._traj


def _walk(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _walk(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v) for v in value]
    return value


def _dropped(text: Any) -> str:
    return f"<dropped:{len(text)} chars>"


def _drop_contents(step: dict[str, Any]) -> None:
    action = step["action"]
    for name in _ACTION_TEXT_FIELDS.get(action.get("type"), ()):
        if isinstance(action.get(name), str):
            action[name] = _dropped(action[name])
    for holder in (step["result"], step["observation"].get("last_action")):
        if holder and holder.get("type") in _RESULT_TEXT_ACTIONS:
            holder["output"] = _dropped(holder["output"])


def redact(traj: Trajectory, *, drop_file_contents: bool = False) -> Trajectory:
    """Return a copy with secrets removed from every string; optionally drop file text (read/search/edit/create
    payloads). ``run_tests`` output is kept: it is the training signal and is already redacted by the executor."""
    out = Trajectory(traj.task_id, traj.seed, copy.deepcopy(traj.fingerprint),
                     [copy.deepcopy(s) for s in traj.steps], copy.deepcopy(traj.outcome))
    if drop_file_contents:
        for step in out.steps:
            _drop_contents(step)
    out.steps = [_walk(s) for s in out.steps]
    return out


def loads_trajectory(text: str) -> Trajectory:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise TrajectoryFormatError("empty trajectory")
    try:
        records = [json.loads(ln) for ln in lines]
    except json.JSONDecodeError as exc:
        raise TrajectoryFormatError(f"invalid JSON line: {exc}") from exc
    header, steps = records[0], records[1:]
    if not isinstance(header, dict) or header.get("kind") != "header":
        raise TrajectoryFormatError("first line must be a header")
    version = header.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise TrajectoryFormatError(f"bad schema_version {version!r}")
    if version > SCHEMA_VERSION:
        raise TrajectoryVersionError(f"trajectory schema {version} is newer than supported {SCHEMA_VERSION}")
    if any(not isinstance(s, dict) or s.get("kind") != "step" for s in steps):
        raise TrajectoryFormatError("every line after the header must be a step")
    return Trajectory(header["task_id"], header["seed"], header["fingerprint"],
                      [{k: v for k, v in s.items() if k != "kind"} for s in steps], header.get("outcome", {}))


def load_trajectory(path: str | Path) -> Trajectory:
    return loads_trajectory(Path(path).read_text(encoding="utf-8"))


def _record(traj: Trajectory) -> dict[str, Any]:
    return {"trajectory_id": traj.trajectory_id, "task_id": traj.task_id, "seed": traj.seed,
            "total_reward": traj.total_reward, "success": traj.success,
            "steps": [{"observation": s["observation"], "action": s["action"]} for s in traj.steps]}


def export_dataset(trajectories: Iterable[Trajectory], kind: str, *, drop_file_contents: bool = False
                   ) -> list[dict[str, Any]]:
    """Build a dataset; the result depends only on the set of trajectories, never on their order.

    ``successful``/``failed``: one record per matching trajectory, sorted by (task, seed, id).
    ``paired``: per task with at least one success and one failure, ``{"task_id", "chosen", "rejected"}`` where
    chosen is the best success (highest reward, then fewest steps, seed, id) and rejected the best failure by the
    same key, the hardest negative. Inputs are redacted first.
    """
    if kind not in DATASET_KINDS:
        raise ValueError(f"unknown dataset kind {kind!r}; expected one of {DATASET_KINDS}")
    safe = [redact(t, drop_file_contents=drop_file_contents) for t in trajectories]

    def order(t: Trajectory) -> tuple[Any, ...]:
        return (-t.total_reward, len(t.steps), t.seed, t.trajectory_id)

    if kind in ("successful", "failed"):
        want = kind == "successful"
        return [_record(t) for t in sorted(safe, key=lambda t: (t.task_id, t.seed, t.trajectory_id))
                if t.success == want]
    pairs = []
    for task_id in sorted({t.task_id for t in safe}):
        group = [t for t in safe if t.task_id == task_id]
        wins, losses = [t for t in group if t.success], [t for t in group if not t.success]
        if wins and losses:
            pairs.append({"task_id": task_id, "chosen": _record(min(wins, key=order)),
                          "rejected": _record(min(losses, key=order))})
    return pairs

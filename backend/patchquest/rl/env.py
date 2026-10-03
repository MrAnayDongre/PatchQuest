"""``PatchQuestEnv``: one evaluation-corpus task as a Gymnasium-shaped episode.

Duck-typed on purpose (no gymnasium dependency): ``reset``/``step``/``close`` follow the Gymnasium 5-tuple
contract and :class:`GymAdapter` adds plain-dict ``observation_space``/``action_space`` (see ``docs/rl-gym.md``
for wrapping with the real library). The production runtime is untouched; this module only reuses its pieces.

Hidden-oracle isolation holds by construction: the workspace is built from the task's *visible* files only. The
oracle files exist in memory and are written, into a throwaway copy, only when the episode ends, so no action can
read, list or search them. Errors from every action are returned as observations, never raised.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from patchquest.evaluation.runner import ORACLE_TIMEOUT, _diff_stats, _materialize, _sha
from patchquest.evaluation.tasks import EvalTask, load_corpus
from patchquest.execution.executor import run_argv, scrubbed_env
from patchquest.memory.repo_indexer import IGNORED_DIRS
from patchquest.patching import CreateFile as CreateChange
from patchquest.patching import SearchReplace, apply_changes
from patchquest.paths import UnsafePathError, resolve_in_repo
from patchquest.rl.actions import (
    ACTION_SCHEMAS,
    Action,
    CreateFile,
    Edit,
    Finish,
    InvalidAction,
    ListDir,
    ReadFile,
    RunTests,
    Search,
    action_type,
    parse_action,
)
from patchquest.rl.reward import RewardBreakdown, RewardConfig
from patchquest.runtime.workspace import ShadowWorkspace, copy_repo, is_secret_file
from patchquest.tools.command_risk import classify
from patchquest.tools.secret_guard import redact_secrets

MAX_SEARCH_HITS = 50
MAX_SEARCH_FILE_BYTES = 1_000_000
_TIMING = re.compile(r"\bin \d+\.\d+s\b|\(\d+(?:\.\d+)?ms\)|duration_ms \d+(?:\.\d+)?")


class EpisodeError(RuntimeError):
    """``step`` was called with no active episode (before ``reset`` or after termination/truncation)."""


@dataclass(frozen=True)
class EnvConfig:
    max_steps: int = 30
    max_command_seconds: float = 30.0
    max_output_bytes: int = 8000


@dataclass
class _Outcome:
    ok: bool
    output: str
    test_delta: float = 0.0
    blocked: bool = False


def _pass_fraction(text: str, success: bool) -> float:
    """Fraction of visible tests passing, from unittest or ``node --test`` output; exit status otherwise."""
    ran = re.search(r"Ran (\d+) tests?", text)
    if ran and int(ran.group(1)) > 0:
        bad = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", text))
        return max(0.0, (int(ran.group(1)) - bad) / int(ran.group(1)))
    passed, failed = re.search(r"^\D*\bpass (\d+)", text, re.M), re.search(r"^\D*\bfail (\d+)", text, re.M)
    if passed and failed and int(passed.group(1)) + int(failed.group(1)) > 0:
        return int(passed.group(1)) / (int(passed.group(1)) + int(failed.group(1)))
    return 1.0 if success else 0.0


class PatchQuestEnv:
    def __init__(self, tasks: list[EvalTask] | None = None, config: EnvConfig | None = None,
                 reward: RewardConfig | None = None) -> None:
        self.tasks = sorted(tasks if tasks is not None else load_corpus(), key=lambda t: t.id)
        self.config = config or EnvConfig()
        self.reward_config = reward or RewardConfig()
        self._root: Path | None = None
        self._task: EvalTask | None = None
        self._ws: ShadowWorkspace | None = None
        self._done = True
        self._seed = 0
        self._last: dict[str, Any] | None = None
        self._steps = 0
        self._counters: dict[str, int] = {}
        self._initial_hashes: dict[str, str | None] = {}
        self._prev_fraction: float | None = None

    # --- Gymnasium surface -----------------------------------------------------------------------
    def reset(self, seed: int | None = None, task_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Start a fresh episode. ``seed`` (default 0) picks the task when ``task_id`` is omitted; the workspace is a
        pure function of the task, so the same (seed, task) gives a byte-identical workspace and first observation."""
        self.close()
        self._seed = 0 if seed is None else seed
        if task_id is None:
            task = self.tasks[self._seed % len(self.tasks)]
        else:
            matches = [t for t in self.tasks if t.id == task_id]
            if not matches:
                raise ValueError(f"unknown task {task_id!r}")
            task = matches[0]
        self._root = Path(tempfile.mkdtemp(prefix="pq-rl-"))
        source = self._root / "source"
        _materialize(source, task.files)
        ws = ShadowWorkspace("episode", str(source), base=self._root / "ws")
        ws.create()
        shutil.rmtree(source)
        self._task, self._ws, self._done = task, ws, False
        self._steps, self._last, self._prev_fraction = 0, None, None
        self._counters = {"steps": 0, "commands": 0, "blocked_commands": 0, "invalid_actions": 0}
        self._initial_hashes = {p: _sha(ws.path / p) for p in task.forbid_changes}
        info = {"task_id": task.id, "seed": self._seed, "category": task.category, "difficulty": task.difficulty,
                "max_steps": self.config.max_steps}
        return self._observe(), info

    def step(self, action: Any) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        if self._task is None or self._done:
            raise EpisodeError("no active episode; call reset()")
        self._steps += 1
        self._counters["steps"] = self._steps
        invalid = False
        kind = "invalid"
        try:
            parsed = parse_action(action)
        except InvalidAction as exc:
            invalid = True
            outcome = _Outcome(False, f"invalid action: {exc}")
            self._counters["invalid_actions"] += 1
        else:
            kind = action_type(parsed)
            outcome = self._execute(parsed)

        terminated = not invalid and isinstance(parsed, Finish)
        truncated = not terminated and self._steps >= self.config.max_steps
        cfg = self.reward_config
        parts: dict[str, float] = {
            "step_cost": -cfg.step_cost,
            "invalid": -cfg.invalid_action if invalid else 0.0,
            "test_fraction": cfg.test_fraction * outcome.test_delta,
            "safety": -cfg.safety_violation if outcome.blocked else 0.0,
        }
        info: dict[str, Any] = {"task_id": self._task.id, "seed": self._seed, "step": self._steps, "invalid": invalid}
        if terminated or truncated:
            self._done = True
            passed, forbidden, changed = self._score_final()
            parts["oracle"] = cfg.oracle_success if passed else 0.0
            parts["patch_size"] = -min(cfg.patch_size_cap, cfg.patch_size_per_line * changed)
            if forbidden:
                parts["safety"] -= cfg.safety_violation
                self._counters["forbidden_changes"] = 1
            info.update(oracle_passed=passed, forbidden_change=forbidden, success=passed and not forbidden,
                        changed_lines=changed)
        breakdown = RewardBreakdown(**parts)
        info["reward_breakdown"] = breakdown
        info["counters"] = dict(self._counters)
        text, was_truncated = self._clean(outcome.output)
        self._last = {"type": kind, "ok": outcome.ok, "output": text, "truncated": was_truncated}
        return self._observe(), breakdown.total, terminated, truncated, info

    def close(self) -> None:
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)
        self._root = self._task = self._ws = None
        self._done = True

    # --- introspection ---------------------------------------------------------------------------
    @property
    def workspace_path(self) -> Path:
        if self._ws is None:
            raise EpisodeError("no active episode; call reset()")
        return self._ws.path

    def fingerprint(self) -> dict[str, Any]:
        """Everything that determines episode behaviour; the oracle only contributes a hash (never its content)."""
        digest = hashlib.sha256(json.dumps(
            [[t.id, t.files, t.test_command, t.oracle_command, t.oracle_files, t.forbid_changes] for t in self.tasks],
            sort_keys=True).encode()).hexdigest()[:16]
        return {"tasks_digest": digest, "python": ".".join(map(str, sys.version_info[:2])),
                "env_config": asdict(self.config), "reward_config": asdict(self.reward_config)}

    # --- observation -----------------------------------------------------------------------------
    def _observe(self) -> dict[str, Any]:
        task = self._require_task()
        return {"task_id": task.id, "instruction": task.task, "file_tree": self._visible_files(),
                "last_action": self._last, "steps_left": self.config.max_steps - self._steps}

    def _require_task(self) -> EvalTask:
        if self._task is None:
            raise EpisodeError("no active episode; call reset()")
        return self._task

    def _clean(self, text: str) -> tuple[str, bool]:
        """Make output safe and reproducible: no host paths or timings, secrets redacted, size-capped."""
        ws = self.workspace_path
        for variant in {str(ws), str(ws.resolve())}:
            text = text.replace(variant, "<workspace>")
        text = redact_secrets(_TIMING.sub("<t>", text))
        raw = text.encode()
        if len(raw) <= self.config.max_output_bytes:
            return text, False
        return raw[: self.config.max_output_bytes].decode(errors="ignore"), True

    def _visible_files(self) -> list[str]:
        root = self.workspace_path
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not (Path(dirpath) / d).is_symlink())
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if not path.is_symlink() and not is_secret_file(name):
                    out.append(path.relative_to(root).as_posix())
        return sorted(out)

    # --- actions ---------------------------------------------------------------------------------
    def _execute(self, action: Action) -> _Outcome:
        try:
            if isinstance(action, ReadFile):
                return self._read_file(action)
            if isinstance(action, ListDir):
                return self._list_dir(action)
            if isinstance(action, Search):
                return self._search(action)
            if isinstance(action, Edit):
                return self._mutate(action.path, SearchReplace(action.path, action.search, action.replace))
            if isinstance(action, CreateFile):
                return self._mutate(action.path, CreateChange(action.path, action.content))
            if isinstance(action, RunTests):
                return self._run_tests()
            return _Outcome(True, "finished")
        except UnsafePathError as exc:
            return _Outcome(False, f"error: {exc}")

    def _resolve_visible(self, rel: str) -> Path:
        """Resolve inside the workspace; credential files, ``.git`` and ignored dirs look like missing paths."""
        root = self.workspace_path.resolve()
        target = resolve_in_repo(str(root), rel)
        parts = target.relative_to(root).parts
        if any(p in IGNORED_DIRS for p in parts) or (parts and is_secret_file(parts[-1])):
            raise UnsafePathError(f"no such path: {rel}")
        return target

    def _read_file(self, action: ReadFile) -> _Outcome:
        target = self._resolve_visible(action.path)
        if not target.is_file():
            return _Outcome(False, f"error: not a file: {action.path}")
        with target.open("rb") as fh:
            raw = fh.read(self.config.max_output_bytes + 1)
        if b"\x00" in raw[:8192]:
            return _Outcome(False, f"error: {action.path} looks binary")
        return _Outcome(True, raw.decode("utf-8", errors="replace"))

    def _list_dir(self, action: ListDir) -> _Outcome:
        target = self._resolve_visible(action.path)
        if not target.is_dir():
            return _Outcome(False, f"error: not a directory: {action.path}")
        names = []
        for entry in sorted(os.scandir(target), key=lambda e: e.name):
            if entry.is_symlink() or entry.name in IGNORED_DIRS or is_secret_file(entry.name):
                continue
            names.append(entry.name + ("/" if entry.is_dir() else ""))
        return _Outcome(True, "\n".join(names) or "(empty)")

    def _search(self, action: Search) -> _Outcome:
        if not action.query:
            return _Outcome(False, "error: empty query")
        root = self.workspace_path
        hits: list[str] = []
        for rel in self._visible_files():
            path = root / rel
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            raw = path.read_bytes()
            if b"\x00" in raw[:8192]:
                continue
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if action.query in line:
                    hits.append(f"{rel}:{number}: {line.strip()[:200]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return _Outcome(True, "\n".join(hits))
        return _Outcome(True, "\n".join(hits) or "no matches")

    def _mutate(self, path: str, change: SearchReplace | CreateChange) -> _Outcome:
        ws = self._ws
        if ws is None:
            raise EpisodeError("no active episode; call reset()")
        self._ensure_baseline()
        ws.note_touched([path])  # records the original so the diff and patch size are exact
        result = apply_changes(str(ws.path), [change])
        if not result.success:
            return _Outcome(False, f"error: {result.error}")
        return _Outcome(True, result.diff or "no change")

    def _run_tests(self) -> _Outcome:
        self._counters["commands"] += 1
        ok, text, blocked = self._visible_tests()
        if blocked:
            self._counters["blocked_commands"] += 1
            return _Outcome(False, text, blocked=True)
        fraction = _pass_fraction(text, ok)
        prev = fraction if self._prev_fraction is None else self._prev_fraction
        self._prev_fraction = fraction
        return _Outcome(ok, text, test_delta=fraction - prev)

    def _visible_tests(self) -> tuple[bool, str, bool]:
        """Run the task's visible test command through the safe executor: ``(ok, output, blocked_by_policy)``."""
        task, ws = self._require_task(), self._ws
        if ws is None:
            raise EpisodeError("no active episode; call reset()")
        decision = classify(task.test_command, str(ws.path))
        if not decision.auto:
            return False, f"command blocked by policy: {decision.reason}", True
        res = run_argv(shlex.split(task.test_command), str(ws.path), timeout=self.config.max_command_seconds,
                       max_output=self.config.max_output_bytes, env=scrubbed_env())
        return bool(res["success"]), f"{res['stdout']}{res['stderr']}", False

    def _ensure_baseline(self) -> None:
        """Measure the pristine pass fraction once, just before the first mutation (not a counted command)."""
        if self._prev_fraction is None:
            ok, text, blocked = self._visible_tests()
            self._prev_fraction = 0.0 if blocked else _pass_fraction(text, ok)

    # --- episode end -----------------------------------------------------------------------------
    def _score_final(self) -> tuple[bool, bool, int]:
        """Oracle verdict, forbidden-change flag, changed-line count. The oracle runs on a throwaway copy."""
        task, ws, root = self._require_task(), self._ws, self._root
        if ws is None or root is None:
            raise EpisodeError("no active episode; call reset()")
        forbidden = any(_sha(ws.path / p) != digest for p, digest in self._initial_hashes.items())
        added, removed, _ = _diff_stats(ws.diff())
        scratch = root / "oracle"
        copy_repo(ws.path, scratch)
        _materialize(scratch, task.oracle_files)  # overwrites any same-named file the agent planted
        try:
            verdict = run_argv(shlex.split(task.oracle_command), str(scratch), timeout=ORACLE_TIMEOUT,
                               env=scrubbed_env())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return bool(verdict["success"]), forbidden, added + removed


class GymAdapter:
    """Same 5-tuple shape as ``gymnasium.Env`` with ``observation_space``/``action_space`` as plain dict schemas."""

    observation_space: dict[str, Any] = {
        "task_id": "str", "instruction": "str", "file_tree": "list[str]",
        "last_action": {"type": "str", "ok": "bool", "output": "str", "truncated": "bool", "nullable": True},
        "steps_left": "int",
    }
    action_space: dict[str, Any] = {"oneof": ACTION_SCHEMAS, "discriminator": "type"}

    def __init__(self, env: PatchQuestEnv) -> None:
        self.env = env

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None
              ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.env.reset(seed=seed, task_id=(options or {}).get("task_id"))

    def step(self, action: Any) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        return self.env.step(action)

    def close(self) -> None:
        self.env.close()

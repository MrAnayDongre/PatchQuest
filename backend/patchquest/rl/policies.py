"""Two tiny reference policies and ``rollout``, for tests and demos (not agents worth training against)."""

from __future__ import annotations

import random
import time
from typing import Any, Protocol

from patchquest.evaluation.tasks import EvalTask, load_corpus
from patchquest.rl.env import PatchQuestEnv
from patchquest.rl.trajectory import Trajectory, TrajectoryRecorder

Observation = dict[str, Any]


class Policy(Protocol):
    def reset(self, observation: Observation) -> None: ...

    def act(self, observation: Observation) -> dict[str, Any]: ...


class OraclePolicy:
    """Replays the corpus task's reference solution (edits, creates), then runs the visible tests and finishes."""

    def __init__(self, tasks: list[EvalTask] | None = None) -> None:
        self._tasks = {t.id: t for t in (tasks if tasks is not None else load_corpus())}
        self._queue: list[dict[str, Any]] = []

    def reset(self, observation: Observation) -> None:
        solution = self._tasks[observation["task_id"]].solution
        self._queue = [
            *({"type": "edit", "path": e["path"], "search": e["search"], "replace": e["replace"]}
              for e in solution.get("edits", [])),
            *({"type": "create_file", "path": c["path"], "content": c.get("content", "")}
              for c in solution.get("create", [])),
            {"type": "run_tests"},
            {"type": "finish"},
        ]

    def act(self, observation: Observation) -> dict[str, Any]:
        return self._queue.pop(0) if len(self._queue) > 1 else {"type": "finish"}


class RandomPolicy:
    """Random actions drawn from the action space, deliberately mixed with hostile paths (traversal, absolute,
    credential files, symlink-ish names) so it doubles as a fuzzer for the workspace boundary."""

    HOSTILE_PATHS = ("../escape.txt", "/etc/passwd", "~/.ssh/id_rsa", "a/../../b", ".git/config", ".env", "")
    WORDS = ("def", "return", "import", "test", "TODO", "self", "x")

    def __init__(self, seed: int = 0, run_tests_prob: float = 0.1) -> None:
        self._seed, self._run_tests_prob = seed, run_tests_prob
        self._rng = random.Random(seed)  # noqa: S311 - reproducible test fuzzing, not security

    def reset(self, observation: Observation) -> None:
        self._rng = random.Random(self._seed)  # noqa: S311 - reproducible test fuzzing, not security

    def _path(self, observation: Observation) -> str:
        files = observation["file_tree"]
        roll = self._rng.random()
        if roll < 0.15:
            return self._rng.choice(self.HOSTILE_PATHS)
        if roll < 0.25 or not files:
            return f"scratch/new_{self._rng.randrange(5)}.txt"
        return self._rng.choice(files)

    def act(self, observation: Observation) -> dict[str, Any]:
        rng = self._rng
        if rng.random() < self._run_tests_prob:
            return {"type": "run_tests"}
        kind = rng.choice(("read_file", "list_dir", "search", "edit", "create_file", "finish", "read_file", "search"))
        if kind == "finish" and observation["steps_left"] > 3 and rng.random() < 0.8:
            kind = "read_file"  # finish is rare early so episodes exercise more of the action space
        path = self._path(observation)
        if kind == "list_dir":
            return {"type": "list_dir", "path": rng.choice((".", "tests", path))}
        if kind == "search":
            return {"type": "search", "query": rng.choice(self.WORDS)}
        if kind == "edit":
            return {"type": "edit", "path": path, "search": rng.choice(self.WORDS), "replace": rng.choice(self.WORDS)}
        if kind == "create_file":
            return {"type": "create_file", "path": path, "content": f"# {rng.randrange(1000)}\n"}
        return {"type": kind, "path": path} if kind == "read_file" else {"type": "finish"}


def rollout(env: PatchQuestEnv, policy: Policy, seed: int = 0, task_id: str | None = None) -> Trajectory:
    """Play one full episode and return its recorded trajectory."""
    observation, info = env.reset(seed=seed, task_id=task_id)
    recorder = TrajectoryRecorder(info["task_id"], info["seed"], env.fingerprint())
    policy.reset(observation)
    done = False
    while not done:
        action = policy.act(observation)
        started = time.monotonic()
        next_observation, _, terminated, truncated, step_info = env.step(action)
        recorder.record(observation=observation, action=action, next_observation=next_observation,
                        terminated=terminated, truncated=truncated, info=step_info,
                        wall_s=time.monotonic() - started)
        observation, done = next_observation, terminated or truncated
    return recorder.finish()

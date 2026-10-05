"""Parallel rollouts: many (task, seed) episodes at once, each in its own environment and workspace.

CPU only. Episodes share nothing (each worker thread builds its own ``PatchQuestEnv``; every episode is a fresh temp
directory), so results do not depend on the worker count: the same jobs give the same trajectories whether run by one
worker or eight, and the returned list is ordered by (task, seed). Use scripted policies to test infrastructure; a
policy that calls a model should bound its own concurrency (one local model, one GPU process).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from patchquest.evaluation.tasks import EvalTask, load_corpus
from patchquest.rl.env import EnvConfig, PatchQuestEnv
from patchquest.rl.policies import Policy, rollout
from patchquest.rl.reward import RewardConfig
from patchquest.rl.trajectory import Trajectory

MAX_WORKERS = 16


@dataclass
class BatchResult:
    trajectories: list[Trajectory]
    errors: list[dict[str, Any]]
    wall_s: float

    def summary(self) -> dict[str, Any]:
        n = len(self.trajectories)
        wins = sum(1 for t in self.trajectories if t.success)
        return {"episodes": n, "errors": len(self.errors), "success_rate": round(wins / n, 4) if n else None,
                "mean_reward": round(sum(t.total_reward for t in self.trajectories) / n, 4) if n else None,
                "mean_steps": round(sum(len(t.steps) for t in self.trajectories) / n, 2) if n else None,
                "wall_s": round(self.wall_s, 2), "episodes_per_s": round(n / self.wall_s, 2) if self.wall_s > 0 and n else None}


def run_batch(policy_factory: Callable[[int], Policy], *, task_ids: Iterable[str] | None = None, seeds: Iterable[int] = (0,),
              workers: int = 4, tasks: list[EvalTask] | None = None, config: EnvConfig | None = None,
              reward: RewardConfig | None = None, out_dir: str | Path | None = None, drop_file_contents: bool = False) -> BatchResult:
    """Roll out every (task, seed) pair. ``policy_factory(seed)`` makes a fresh policy per episode.

    A failing episode is recorded in ``errors`` and does not stop the others. With ``out_dir`` each trajectory is written
    as ``<task>-s<seed>.jsonl`` (redacted).
    """
    corpus = tasks if tasks is not None else load_corpus()
    wanted = sorted(set(task_ids) if task_ids is not None else {t.id for t in corpus})
    known = {t.id for t in corpus}
    if unknown := set(wanted) - known:
        raise ValueError(f"unknown task(s): {', '.join(sorted(unknown))}")
    jobs = [(task, seed) for task in wanted for seed in sorted(set(seeds))]
    workers = max(1, min(workers, MAX_WORKERS, len(jobs) or 1))
    destination = Path(out_dir) if out_dir else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)

    def play(job: tuple[str, int]) -> Trajectory:
        task_id, seed = job
        env = PatchQuestEnv(tasks=corpus, config=config, reward=reward)
        try:
            traj = rollout(env, policy_factory(seed), seed=seed, task_id=task_id)
        finally:
            env.close()
        if destination:
            traj.dump(destination / f"{task_id}-s{seed}.jsonl", drop_file_contents=drop_file_contents)
        return traj

    started = time.monotonic()
    done: list[Trajectory] = []
    errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pq-rollout") as pool:
        futures = [(job, pool.submit(play, job)) for job in jobs]
        for (task_id, seed), future in futures:
            try:
                done.append(future.result())
            except Exception as exc:  # one broken episode must not lose the rest
                errors.append({"task_id": task_id, "seed": seed, "error": f"{type(exc).__name__}: {exc}"[:300]})
    done.sort(key=lambda t: (t.task_id, t.seed))
    return BatchResult(done, errors, time.monotonic() - started)

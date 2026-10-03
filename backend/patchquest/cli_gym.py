"""``patchquest gym ...`` (simulated episodes, in parallel) and ``patchquest trajectory RUN`` (a real run as a trajectory)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def register(sub: Any) -> None:
    gym = sub.add_parser("gym", help="roll out episodes of the evaluation tasks and build datasets (CPU only, no model)").add_subparsers(dest="gym_cmd", required=True)
    ro = gym.add_parser("rollout", help="play many (task, seed) episodes in parallel and write one JSONL trajectory each")
    ro.add_argument("--policy", choices=["oracle", "random"], default="oracle", help="oracle replays the reference solution; random fuzzes the boundary")
    ro.add_argument("--task", action="append", help="task id (repeat); default all")
    ro.add_argument("--seeds", type=int, default=1, help="episodes per task, seeds 0..N-1")
    ro.add_argument("--workers", type=int, default=4)
    ro.add_argument("--out", required=True, help="directory for the trajectories")
    ro.add_argument("--drop-file-contents", action="store_true")
    ro.add_argument("--json", action="store_true")
    ds = gym.add_parser("dataset", help="build a dataset from a directory of trajectories")
    ds.add_argument("directory")
    ds.add_argument("--kind", choices=["successful", "failed", "paired"], default="paired")
    ds.add_argument("--out", help="write JSONL here (default: stdout)")
    tr = sub.add_parser("trajectory", help="export a real run as a training/evaluation trajectory (JSONL) with its reward breakdown")
    tr.add_argument("run_id")
    tr.add_argument("--out")
    tr.add_argument("--model-io", action="store_true", help="include prompts and responses (only if the run recorded them)")
    tr.add_argument("--summary", action="store_true", help="print only the reward and totals")


def run(args: argparse.Namespace) -> int:
    if args.cmd == "trajectory":
        from patchquest.database import get_db
        from patchquest.rl import production

        try:
            with get_db() as conn:
                traj = production.build(conn, args.run_id, include_model_io=args.model_io)
        except LookupError:
            print(f"error: no such run: {args.run_id}", file=sys.stderr)
            return 1
        if args.summary:
            print(json.dumps({"run": traj["run"]["id"], "reward": traj["reward"], "totals": traj["totals"]}, indent=2))
        elif args.out:
            Path(args.out).write_text(production.to_jsonl(traj))
            print(f"wrote {len(traj['steps'])} steps to {args.out}")
        else:
            sys.stdout.write(production.to_jsonl(traj))
        return 0

    from patchquest.rl import OraclePolicy, RandomPolicy, export_dataset, load_trajectory, run_batch

    if args.gym_cmd == "rollout":
        factory: Any = (lambda seed: OraclePolicy()) if args.policy == "oracle" else (lambda seed: RandomPolicy(seed))
        try:
            result = run_batch(factory, task_ids=args.task, seeds=range(args.seeds), workers=args.workers, out_dir=args.out,
                               drop_file_contents=args.drop_file_contents)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        summary = result.summary()
        print(json.dumps(summary, indent=2) if args.json else
              f"{summary['episodes']} episodes ({summary['errors']} errors), success {summary['success_rate']}, mean reward {summary['mean_reward']}, "
              f"{summary['episodes_per_s']} episodes/s -> {args.out}")
        return 0 if not result.errors else 1
    trajectories = [load_trajectory(p) for p in sorted(Path(args.directory).glob("*.jsonl"))]
    records = export_dataset(trajectories, args.kind)
    text = "".join(json.dumps(r) + "\n" for r in records)
    if args.out:
        Path(args.out).write_text(text)
        print(f"{len(records)} {args.kind} records from {len(trajectories)} trajectories -> {args.out}")
    else:
        sys.stdout.write(text)
    return 0

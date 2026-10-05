"""``python -m patchquest.rl.rollout --task X --policy oracle --out f.jsonl``: run one episode, export it."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from patchquest.rl.env import EnvConfig, PatchQuestEnv
from patchquest.rl.policies import OraclePolicy, RandomPolicy, rollout


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patchquest.rl.rollout", description=__doc__)
    parser.add_argument("--task", required=True, help="evaluation task id")
    parser.add_argument("--policy", choices=("oracle", "random"), default="oracle")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=EnvConfig.max_steps)
    parser.add_argument("--out", help="write the trajectory as JSONL here")
    parser.add_argument("--drop-file-contents", action="store_true", help="omit file text from the export")
    args = parser.parse_args(argv)

    env = PatchQuestEnv(config=EnvConfig(max_steps=args.max_steps))
    try:
        policy = OraclePolicy() if args.policy == "oracle" else RandomPolicy(args.seed)
        try:
            traj = rollout(env, policy, seed=args.seed, task_id=args.task)
        except ValueError as exc:  # unknown task id
            print(f"error: {exc}", file=sys.stderr)
            return 2
    finally:
        env.close()
    if args.out:
        traj.dump(args.out, drop_file_contents=args.drop_file_contents)
    print(json.dumps({"task": traj.task_id, "policy": args.policy, "success": traj.success,
                      "total_reward": traj.total_reward, "steps": len(traj.steps)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

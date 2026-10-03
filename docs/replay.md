# Replay and fork

Both create **new runs** that point at the run they came from (`parent_run_id`, `lineage_kind`). The parent
is never modified. `patchquest lineage <run>` shows ancestry and children.

## Replay

```
patchquest replay <run> --mode state   # default
patchquest replay <run> --mode model
patchquest replay <run> --mode live
```

| Mode | What it does | Side effects |
|---|---|---|
| `state` | rebuilds status trail and phases **from the ledger alone** and checks it: legal transitions, checkpoint integrity, status vs. history | none (read-only) |
| `model` | re-runs PatchQuest's orchestration; model answers are the recorded ones, in order | shadow workspace only |
| `live` | same, but asks the model again | shadow workspace + model calls |

Model and live replays set `promote_policy=never`: they leave a diff and **never write the repository**.
The shadow workspace starts from the files the original saw (their pre-change contents come from the
checkpoint), so a replay of a run whose fix was promoted still reproduces. `compare` reports where a replay
and its original differ: verdict, final diff, phase sequence, repair rounds, model-call count - not the
outcome, because a replay never promotes. A replay that asks for something the recording does not hold
(different role, or no answers left) stops as `REPLAY_DIVERGED`.

Requires `agent.record_model_io` (default on); otherwise the model's answers were not kept.

## Fork

```
patchquest fork <run> [--from <checkpoint>] [--model M] [--provider P] [--base-url U] [--set agent.KEY=VALUE]
```

Starts from one of the run's checkpoints (default: latest valid) with a different model/provider and/or
settings. The child owns a copy of the checkpoint (so it can itself be resumed) and keeps its own identity.
If the repository changed since that checkpoint the fork is refused unless `--accept-drift`.

**Overrides are per run and limited to `agent.*`.** `safety.*` cannot be overridden: a run or fork must not
be able to weaken the command policy or approval rules. Overrides are validated before anything is created
and re-applied on resume.

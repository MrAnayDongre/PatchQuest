# Evaluation

Evaluation is a product feature: it is how you find out whether a model, a setting or a change to PatchQuest made things better,
and *why* a task failed. Everything below runs from `patchquest eval ...`, writes machine-readable JSON, and (apart from the
live tiers) needs no model and no GPU.

## The corpus

14 tasks (`backend/patchquest/evaluation/corpus/*.yaml`): bug fixes, a feature, a refactor, security fixes, a test repair,
multi-file and API changes, JavaScript. Each has fixture files, an instruction, a visible test command, a **hidden oracle** (files
and a command added only *after* the agent finishes, so it cannot influence the run), optional `forbid_changes`, and a reference
solution. Results record the corpus digest; comparing results from different corpora is refused.

Outcome of a task: `success` (applied and the oracle passes), `partial` (applied but the hidden checks fail, or a forbidden file
changed), `failure`. The failure *reason* (`declined`, `no_patch`, `validation_failed`, `conflict`, `oracle_failed`, ...) says what happened.

## Failure attribution

Every non-success is attributed to one party, so a harness bug is never blamed on a model nor a weak model on the harness:

`HARNESS_FAILURE` `MODEL_FAILURE` `PROVIDER_FAILURE` `TOOL_FAILURE` `SANDBOX_FAILURE` `ENVIRONMENT_FAILURE` `ORACLE_FAILURE` `TIMEOUT` `BUDGET`

From the run's typed failure kind where there is one (`MODEL_AUTH` -> provider, `PATCH_APPLY` -> model, `INTERNAL_INVARIANT` -> harness ...),
else from the reason. Two rules are absolute: a failure in a **scripted** run (no model, the reference solution) is always the
harness's, and an oracle that could not even run (exit 126/127, killed) is `ORACLE_FAILURE`, while one whose hidden tests merely failed is the model's.

## Commands

```bash
patchquest eval run --provider sglang --model Qwen/Qwen3-0.6B --base-url http://localhost:30000/v1 --out new.json
patchquest eval compare old.json new.json          # regressions = succeeded before, not now (exit 1)
patchquest eval recovery                           # crash / resume / drift / corruption / cancel scenarios
patchquest eval matrix --target sglang:Qwen/Qwen3-0.6B@http://localhost:30000/v1 --target openai:gpt-4o-mini
patchquest eval experiment --provider sglang --model M --baseline agent.max_repair_rounds=1 --candidate agent.max_repair_rounds=3
patchquest eval gate --tier 2                      # regression gates 0..4
```

**Recovery scenarios** (no model): killed after the patch checkpoint / mid-command / before and after the repository write /
before any checkpoint; a person edits the same file while the run is down; the newest checkpoint is corrupted; the user cancels.
Each must end correct and safe: the repository written exactly once or not at all, a person's edit never overwritten, completed
model work not repeated (compared against an uninterrupted baseline), and what cannot be proven safe waiting for a person. The suite
is itself tested to *fail* when the drift check is blinded.

**Experiments** pair the same tasks under a baseline and a candidate `agent.*` setting and report wins, losses, ties and the exact
two-sided **sign test**. With a handful of decisive tasks nothing is significant, and the output says so instead of showing a trend.

**Gates** (cheapest first; a tier passes only if the lower ones did; a skipped live tier is *not* passed):

| Tier | Checks |
|---|---|
| 0 harness | every reference solution passes **and** a null solution (no change) fails every task: the oracle is neither broken nor vacuous |
| 1 recovery | all recovery scenarios |
| 2 replay | each corpus run replayed from its recorded answers matches the original |
| 3 live smoke | 3 tasks on a real model: no harness / provider / sandbox / environment / oracle / tool failures (the model being wrong is allowed) |
| 4 live corpus | full corpus vs a baseline file: no regressions, success rate >= `--fail-under` |

## What the numbers say today

Scripted reference solutions: 14/14 (tier 0). Recovery: 8/8 scenarios. Replay: 14/14. Live, Qwen3-0.6B on SGLang: 0/14 (best earlier
run 1/14; see `docs/evals/` and the README). The corpus is small (14 tasks) and one model has been measured; treat live numbers as a
baseline for improvement, not a ranking.

## Not built

Context-quality metrics (relevant-file recall, irrelevant-context ratio), a provider benchmark with cost, parallel rollouts across
machines, and a 'long-horizon workflow' task class.

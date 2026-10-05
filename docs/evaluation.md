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

## Context quality and incremental indexing

`patchquest eval context` scores how well the files shown to a model match what a person would need, with no model and no
GPU: a 42-file synthetic repository (packages with overlapping vocabulary, two modules with the same job, tests beside code,
a TypeScript front end, docs that mention everything) and 12 tasks, each with its ground-truth files and symbols
(`evaluation/context_fixture`). Metrics: **file recall**, **symbol recall** (the definition is in the selected text),
**precision**, **irrelevant-context ratio** (characters read for nothing), tokens, and selection latency.

Measured on the shipped strategies (one laptop, Python 3.12; raw numbers in `docs/evals/context-quality-baseline.json`):

| Strategy | file recall | symbol recall | precision | irrelevant | mean tokens |
|---|---|---|---|---|---|
| `lexical` (default) | 89% | 88% | 54% | 34% | 160 |
| `focused` (`agent.context_strategy: focused`) | 89% | 88% | 69% | 20% | 130 |

`focused` drops candidates scoring under a quarter of the best and attaches tests only to the two best sources. On this
fixture that kept recall in every case (0 worse, paired) and cut about a fifth of the tokens. **It is not the default**: 12 cases
on a synthetic repository are evidence that it is worth trying, not proof it is better on your code - compare with
`patchquest eval experiment --baseline agent.context_strategy=lexical --candidate agent.context_strategy=focused` on real tasks.
The sweep behind the choice (cutoff 0.25-0.7) trades recall for precision monotonically; 0.25 was the last point with no recall loss.

**Known miss, reported not hidden:** the `decimals` case ("amounts are displayed with one decimal") shares no word with the file
or symbol names, so lexical selection finds nothing. Closing that gap needs semantic retrieval, which would be justified by this
number, not assumed.

**Index.** The repository index is incremental: unchanged files (same size and timestamp) are not read, touched-but-identical files
are not re-parsed, changed files' symbols are replaced, deleted files leave the index. On the fixture: cold 5 ms; unchanged
re-index 1.3 ms with 0 files reprocessed; after 3 edits + 2 deletions + 2 additions 3.3 ms with 5 of 42 files reprocessed
(88% cache hits); the share of index entries that no longer matched the disk went from 12% to 0%. (Before this work every run
appended its symbols again - the table grew without bound - and deleted files stayed indexed; migration 16 removes the duplicates.)
Timings are for a tiny repository; they show what is reused, not how a large monorepo behaves.

# The PatchQuest runtime

A run is a sequence of phases (intake, repo scan, planning, research, context, analysis, patching, static
checks, testing, review, security scan, report) executed by `RunStateMachine`. Everything that matters
about a run is **persisted as it happens**, so the process can die at any moment and the run can be
inspected, resumed, replayed or forked afterwards.

| Concern | Where it lives | Document |
|---|---|---|
| What happened, in order | the event ledger (`run_events`) | [events.md](events.md) |
| What state to continue from | checksummed checkpoints | [checkpoints.md](checkpoints.md) |
| Continuing after a crash | `patchquest resume` | [failure-recovery.md](failure-recovery.md) |
| Re-running without side effects, forking | `patchquest replay` / `fork` | [replay.md](replay.md) |
| Human decisions | the approval engine | [approvals.md](approvals.md) |

## Invariants

1. **Status changes only through the transition table** (`domain/runs.py`). Every change is
   compare-and-set, records actor, reason, attempt and correlation id, and commits in the same transaction
   as its ledger event. An illegal change raises and changes nothing.
2. **The ledger is append-only.** Database triggers refuse `UPDATE` and `DELETE`; the one exception is
   redaction, which blanks content but keeps the event.
3. **The real repository is written exactly once**, at promotion, after validation, with sha256
   preconditions, and only after `promotion_started` is journaled.
4. **Nothing external is repeated blindly.** Resume classifies what it is unsure of; uncertain
   promotion is reconciled against file hashes, never re-run.
5. **One place decides retries** (`runtime/retry.py`), from a typed failure (`domain/failures.py`).
6. **Cancellation reaches whatever is running** - subprocess trees, model calls, approval waits - except a
   promotion already under way, which finishes (it is atomic) and then the cancel takes effect.

## Run status

```
created -> running -> completed | failed | cancelled
running <-> waiting_approval
running|waiting_approval -> cancel_requested -> cancelled (or completed if the patch had already landed)
running|waiting_approval|cancel_requested -> interrupted -> running (resume)
```

`completed`, `failed` and `cancelled` are final. `interrupted` is what startup recovery makes of a run
whose process died; it is the only state resume accepts.

## Phases and checkpoints

After every phase that settles (complete, skipped or blocked) the runtime writes a checkpoint
containing the run context, phase statuses, the machine's steering flags, the shadow workspace's touched
files and a repository fingerprint. A failed phase is not checkpointed - the run ends there.

## Budgets

Model calls, tokens, wall time, commands, patch attempts, repair rounds and retries each have a limit
(`agent.max_*`, `0` = unlimited) and a counter on the run context, so consumption survives resume. Every
checkpoint event carries the current budget; `patchquest status <run>` prints it. Exceeding a limit fails
the run with `BUDGET_EXHAUSTED`; fork it with a higher limit to continue.

## Honest limits

- Wall time accrued between the last checkpoint and a crash is not counted after resume.
- Tool results are not recorded for replay; model replay re-executes commands in the shadow workspace.
- Single-process, single-database (SQLite). Multi-worker leases and Postgres are designed, not built.

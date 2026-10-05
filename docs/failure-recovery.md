# Failures, retries, resume

## Failure taxonomy

Every failure is a `FailureKind` with a fixed spec (`domain/failures.py`): whether retrying can help,
severity, origin, a message for people, and what they can do. The failed run records the kind
(`runs.failure_kind`); events carry the full payload. Examples: `MODEL_TIMEOUT` (retryable),
`MODEL_AUTH`, `MODEL_CONTEXT_OVERFLOW`, `PATCH_APPLY`, `COMMAND_DENIED`, `REPOSITORY_DRIFT`,
`BUDGET_EXHAUSTED`, `USER_CANCELLED`, `REPLAY_DIVERGED`, `INTERNAL_INVARIANT`. An exception nobody
recognised is `INTERNAL_INVARIANT` and is never retried.

## Retry rules (`runtime/retry.py`)

In order: never retry what retrying cannot fix or must not repeat (denials, cancellation, budget, auth,
policy, malformed output, drift); never repeat an operation that may already have taken effect unless it is
idempotent; respect the attempt cap and the run's retry budget; then wait - full-jitter exponential backoff,
or exactly the server's `Retry-After` (capped at 120 s). Cancellation is never swallowed. Providers do not
retry on their own; model calls are idempotent and retried here, each as a `retry_scheduled` event.

## Resume

```
patchquest resume <run> [--plan] [--accept-drift] [--rollback]
```

Before anything runs it prints:

```
LAST_CHECKPOINT  #7 after patching (...)
INTERRUPTED_OPERATION  phase 'testing'; command `python3 -m unittest ...` was running
REPO_DRIFT  NO_DRIFT
SIDE_EFFECT_CERTAINTY  NONE | VERIFIED | UNCERTAIN
RECOVERY_ACTION  Continue from phase 'static_checks'
APPROVAL_REQUIRED  false
```

| Category | When | What happens |
|---|---|---|
| `SAFE_RESUME` | valid checkpoint, no drift or only safe drift | continue from the next phase |
| `SAFE_RETRY` | no usable checkpoint, nothing external happened | start again from the beginning |
| `HUMAN_CONFIRMATION_REQUIRED` | conflicting/unknown drift, or promotion state that cannot be reconciled | stop until `--accept-drift` |
| `ROLLBACK_REQUIRED` | promotion wrote some files and not others | stop until `--rollback` restores them |
| `NON_RECOVERABLE` | run already ended, or still active | refuse |

Exit codes: `0` ok, `1` failed, `2` rejected, `3` interrupted, `4` a person must decide first, `64` usage.

### Promotion journal

`promotion_started` (with each file's expected `base` and `new` sha256) is journaled **before** the first
write; `promotion_completed`/`promotion_failed` after. A crash in between is settled by hashing: all files
original -> promotion runs again; all new -> it had landed and is not repeated (`promotion_reconciled`);
mixed -> rollback required (only files whose content is exactly what PatchQuest wrote are restored, hash
checked); anything else -> a person decides. A human's edits are never overwritten.

## Cancellation

`cancel` sets one signal that stops subprocess trees (process-group kill, container removal for Docker),
interrupts an in-flight model call, and wakes an approval wait as a denial. A promotion already under way
finishes. A cancel in the last phase ends the run `cancelled` unless the patch had already landed.

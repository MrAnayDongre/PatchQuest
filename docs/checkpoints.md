# Checkpoints

A checkpoint is one SQLite row, written in one transaction after each settled phase.

## Contents

Header: run, `seq`, schema version, runtime version, the phase just completed, ledger cursor, attempt.
State: the run context (plan, selected context, test results, budgets, ...), phase statuses, the machine's
flags, and the shadow workspace as the touched files' **original and current bytes**. Fingerprint: git HEAD,
branch, a hash of `git status`, and sha256 of every file the run read or will write.

Not stored: identity (run id, provider, model, repo path) - that belongs to the run being executed, so a
fork never believes it is its parent. Command and test output is secret-redacted before it is written.

## Integrity

Each row carries a SHA-256 over its identity and content. On load a truncated, edited or bit-rotted row is
**detected and skipped** in favour of the previous good checkpoint. A row from an older schema is upgraded
on read; one from a newer PatchQuest is refused. Size is capped (16 MiB); a checkpoint that cannot be saved
is recorded as `checkpoint_failed` and the run continues, resumable only from an earlier one.

```
patchquest checkpoints <run> [--json]     # sequence, phase, size, integrity status
```

## Repository drift

`runtime/fingerprint.py` compares the recorded repository with the current one:

| Result | Meaning | Resume |
|---|---|---|
| `NO_DRIFT` | unchanged | continues |
| `SAFE_DRIFT` | repository moved, but not files this run writes | continues (input files that changed are reported) |
| `CONFLICTING_DRIFT` | a file the run modifies changed | needs `--accept-drift`; promotion still refuses to overwrite it |
| `UNKNOWN_DRIFT` | cannot compare | needs `--accept-drift` |

Git is only ever read, with the repository's own configuration neutralised (`core.fsmonitor`, hooks): an
untrusted repo's `.git/config` can otherwise execute commands during `git status`. This is covered by a test
that fails without the hardening.

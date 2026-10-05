# The event ledger

`run_events` is an append-only, versioned, attributable record of everything a run did.

## Row

| Field | Meaning |
|---|---|
| `id` | global monotonic cursor; clients resume streams with `after=<id>` |
| `event_uid` | unique id of the event |
| `schema_version` | version of the event schema (currently 1) |
| `run_id`, `type`, `phase`, `status`, `message`, `payload` | what happened |
| `actor` | `runtime`, `user:<id>`, `cli`, `recovery`, `resume`, `replay` |
| `attempt` | 1 for the first execution, +1 for each resume |
| `correlation_id` | groups the events of one execution attempt |
| `causation_id` | the `phase_started` event that caused this one |
| `redacted` | set when the content was blanked by `ledger.redact` |

## Guarantees

- **Immutable.** Triggers abort any `UPDATE`/`DELETE`, except a redaction (`payload`/`message` -> NULL,
  `redacted = 1`, identity untouched). A redacted event cannot be edited further.
- **Ordered.** `id` is monotonic. Reading with a cursor returns exactly the events after it.
- **Atomic with state.** Status changes, approval decisions and checkpoints commit together with their event.
- **Legacy rows** written before the ledger had these columns keep `event_uid = NULL`; nothing is rewritten.

## Event types

Lifecycle: `run_created`, `run_state_changed`, `run_resume_requested`, `run_interrupted`, `run_completed`, `run_failed`.
Phases: `phase_started`, `phase_completed`, `phase_skipped`, `phase_blocked`, `phase_failed`.
Models: `model_call`, `retry_scheduled`.
Patches: `patch_proposed`, `patch_staged`, `patch_retry`, `patch_empty`, `patch_missing`, `patch_rejected`,
`patch_applied`, `repair_started`, `tests_started`, `tests_completed`, `baseline_started`.
Commands: `command_started`, `command_executed`, `command_denied`, `command_blocked`.
Approvals: `approval_requested`, `approval_decided`, `approval_expired`, `approval_reused`.
Promotion: `promotion_started`, `promotion_completed`, `promotion_failed`, `promotion_reconciled`, `promotion_rolled_back`.
Durability: `checkpoint_created`, `checkpoint_failed`, `workspace_created`.
Lineage: `fork_created`, `replay_created`, `replay_completed`, `replay_diverged`.
Failures carry a typed `failure` payload (kind, retryable, severity, origin, message, recovery options).

## Reading

```
patchquest events <run> [--after N] [--json]
GET /api/runs/<run>/stream?after_id=N      (server-sent events)
```

## Schema changes

`schema_version` and the migration list (`persistence/schema.py`) are how this evolves. A database written
by a newer PatchQuest is refused (`SchemaTooNew`); before the first migration of an existing database a
backup (`<db>.pre-v<N>.bak`) is written.

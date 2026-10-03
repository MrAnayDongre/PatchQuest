# 1. An append-only, database-enforced event ledger

**Status:** accepted

## Context
Run history was a plain table that code could update or delete, with no attribution, versioning or causal
links. Replay, audit, crash analysis and the UI all need a history they can trust.

## Decision
`run_events` is append-only, enforced by SQLite triggers (not by convention). Each row has a unique id, a
schema version, actor, attempt, correlation id and causation id. The only permitted mutation is redaction,
which blanks content and sets a flag. Status changes and approval decisions are written in the same
transaction as their events.

## Consequences
+ History cannot be rewritten by a bug; replay and audit can rely on it.
+ Redaction is possible without breaking ordering or counts.
- Deleting a run's history (retention) needs a deliberate, separate mechanism; there is none yet.
- Event volume is unbounded per run; archival is future work.

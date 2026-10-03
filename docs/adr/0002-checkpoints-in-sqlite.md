# 2. Checkpoints are checksummed rows in the primary database

**Status:** accepted

## Context
Resume needs state that survives a crash, and must not trust a half-written or damaged copy.

## Decision
One row per settled phase, written in a single transaction (atomic), with a SHA-256 over identity and
content verified on every read. A bad row is skipped for the previous good one. State is plain JSON (no
pickles); the shadow workspace is captured as touched files' original and current bytes, not a directory.

## Alternatives rejected
- Files on disk: needs its own atomicity and cleanup; two stores to keep consistent.
- Pickling the state machine: not versionable, unsafe to load, captures behaviour not state.

## Consequences
+ One store, one backup, one transaction boundary with the ledger.
+ Forks and replays can start from any checkpoint.
- Large contexts inflate the database (16 MiB cap per checkpoint). Moving blobs to an artifact store is future work.

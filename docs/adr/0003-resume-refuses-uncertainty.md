# 3. Resume classifies uncertainty instead of guessing

**Status:** accepted

## Context
After a crash the interesting question is not "can we continue" but "did the last thing happen".

## Decision
Resume produces a plan with a category (SAFE_RESUME, SAFE_RETRY, ROLLBACK_REQUIRED,
HUMAN_CONFIRMATION_REQUIRED, NON_RECOVERABLE) and explains it before acting. The one write to the real
repository is journaled before it starts and reconciled afterwards by comparing file hashes with the
journal. Anything that cannot be proven safe waits for a person; rollback only touches files whose content
is exactly what PatchQuest wrote.

## Consequences
+ A crash can never cause a repeated or silently half-applied write, or overwrite a human's edit.
- More runs stop and wait than a "just retry" design would. That is deliberate.

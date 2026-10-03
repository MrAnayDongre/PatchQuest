# 4. Per-run configuration overrides cannot touch safety policy

**Status:** accepted

## Context
Forks and experiments need different settings per run, but the configuration is process-global and
concurrent runs must not see each other's values.

## Decision
Overrides are carried in a context variable (visible to the awaited work and worker threads of one run) and
persisted on the run so resume re-applies them. Only `agent.*` settings are overridable, validated against
the schema before anything is created. `safety.*` is deliberately not overridable.

## Consequences
+ A run, fork or experiment cannot weaken the command policy or approval rules by changing its own config.
+ No global state is mutated, so concurrent runs are isolated.
- Anything that should be tunable per run but lives outside `agent.*` needs an explicit decision.

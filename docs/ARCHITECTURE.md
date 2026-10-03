# PatchQuest architecture

A **modular monolith with a durable worker runtime**: strong internal boundaries, one deployable, a primary database.

```
        UI (frontend/)        CLI (patchquest)        HTTP clients
              \                    |                    /
               +------ application / API (control plane) ------+
               |  auth, tenancy, approvals, run lifecycle       |
               +---------+----------------+---------------------+
                         |                |
                 durable runtime     workflow engine ---- connectors (webhooks, GitHub, SSRF guard)
        (state machine, ledger,           |
         checkpoints, resume, replay,     |
         queue + workers, budgets)        |
                         |                |
               agents, providers, tools, sandbox, repo intelligence
                         |
               persistence (SQLite: ledger, checkpoints, identity, audit, queue)
                         |
               observability (metrics, OTLP traces) - derived from the ledger
```

## Packages and the rule between them

Dependencies point **down** this list; a package never imports from one above it.

| Package | Responsibility |
|---|---|
| `domain/` | pure types and rules: run status machine, failure taxonomy, side effects, approvals, identity/roles, budgets, workflow definitions. No I/O. |
| `persistence/` | migrations, the append-only ledger, validated run transitions, checkpoints, approvals, identity, queue-agnostic SQL |
| `runtime/` | fingerprints and drift, resume planning, replay, lineage/fork, retry engine, queue + worker, workspace and sandbox |
| `orchestrator/` | `RunStateMachine` (12 phases), snapshot codec, event bus |
| `agents/`, `providers/` | model roles and prompts, provider contract and adapters, structured output, budgeting, failover, health |
| `patching/`, `tools/`, `execution/`, `validation/`, `context/`, `memory/` | verified edits, command policy and execution, test running, context selection, repo index |
| `application/` | `TaskService`: the one place API and CLI go through (create, launch, resume, fork, replay, decide, enqueue) |
| `workflows/`, `connectors/` | durable workflow engine and templates; webhook/outbound/SSRF/connector contract |
| `observability/`, `evaluation/`, `rl/` | metrics, traces; the evaluation corpus and runner; the agent gym |
| `api/`, `cli.py`, `main.py` | thin adapters: auth dependencies, routes, commands |

## One run

`created -> (queued) -> running -> completed | failed | cancelled`, with `waiting_approval`, `cancel_requested` and
`interrupted` in between. Phases: intake, scan, plan, research, context, analysis, **patch** (in a shadow workspace),
static checks, **test** (repair loop, baseline attribution), review, security scan, report/promote. After each settled
phase a checksummed checkpoint is written. The real repository is touched once, at promotion, after validation, with
sha256 preconditions, journaled before the first byte is written. See [runtime.md](runtime.md).

## Where state lives

| What | Where | Properties |
|---|---|---|
| History of a run | `run_events` | append-only (triggers), versioned, attributed, cursor-ordered |
| Resumable state | `checkpoints` | one row per settled phase, sha256-verified, versioned |
| Run status | `runs` | changes only via the transition table, compare-and-set, recorded in the ledger |
| Who/what/may | `organizations workspaces principals memberships api_tokens` | hashed tokens, per-workspace roles |
| Security events | `audit_log` | append-only |
| Work distribution | `runs.status = queued`, lease columns | exclusive claim, heartbeat, expiry recovery |
| Workflows | `workflows workflow_runs workflow_steps workflow_events` | versioned definitions, durable steps |
| Connectors | `connector_events webhook_deliveries` | per-workspace dedup, delivery log with dead-letter |
| Shadow workspaces | `~/.patchquest/sandboxes/<run>/` | disposable; rebuilt from a checkpoint after a crash |

Everything the UI, metrics and traces show is *derived* from these rows, so a refresh, a crash or a replay shows the same thing.

## Extension points

- **Provider:** implement `ProviderBase`, register in `provider_registry.py`, add a catalogue entry. Capabilities are
  declared and degradation is explicit; no provider-specific branches in agent code.
- **Connector:** implement `Connector._execute`/`find_existing`/`verify`/`normalize` ([connectors.md](connectors.md)); the base class enforces grants and reconciliation.
- **Workflow action:** add to `workflows/catalog.py` with its side-effect class; validation will then require a human gate for writes.
- **Evaluation task:** a YAML file in `evaluation/corpus/` with a hidden oracle ([evals](evals/)).
- **Migration:** append to `persistence/schema.py` (never edit a shipped one).

Not built: a plugin loader, a vector index, PostgreSQL. See [deployment.md](deployment.md) and the README status table.

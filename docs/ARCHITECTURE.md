# PatchQuest Architecture

This document describes how the backend, frontend, and runtime layers fit together. For setup and usage, see the [README](../README.md).

## Design principles

1. **Deterministic orchestration** — phase order is fixed; LLM output does not decide what runs next.
2. **Event sourcing** — run state transitions are append-only events in SQLite, replayable for UI and reports.
3. **Role isolation** — each agent role receives constrained context and structured output schemas.
4. **Safety first** — command risk and SecretGuard run before shell execution; approvals gate risky operations.
5. **Local-first** — SQLite persistence, optional offline mock mode, no required cloud services.

## Backend layout

```
backend/patchquest/
├── main.py              FastAPI app, router registration, scheduler startup
├── config.py            YAML config loading (sample.config.yaml)
├── database.py          SQLite schema, migrations, event insertion
├── api/                 HTTP routes (runs, reports, scheduler, search, …)
├── orchestrator/
│   ├── state_machine.py Phase execution loop
│   ├── phases.py        Phase enum and ordering (12 phases)
│   ├── run_context.py   Mutable per-run state passed through phases
│   ├── event_bus.py     In-process pub/sub for SSE fan-out
│   └── approvals.py     Pending approval resolution
├── agents/
│   ├── provider_registry.py   Provider name → implementation
│   ├── roles.py               Role-specific LLM calls
│   ├── prompts.py             System instructions per role
│   └── providers_*.py         OpenAI, Anthropic, NVIDIA, mock, …
├── tools/
│   ├── secret_guard.py        Pattern detection and redaction
│   ├── command_risk.py        Deterministic command classification
│   ├── command_runner.py      Local and sandboxed execution
│   ├── patch_tools.py         Diff apply with safety checks
│   └── file_tools.py          Workspace-scoped file I/O
├── memory/
│   ├── repo_indexer.py        File scan and language detection
│   ├── tree_sitter_*.py       AST symbol extraction
│   ├── code_graph.py          Symbol dependency graph
│   └── memory_store.py        Per-repo fact persistence
├── runtime/
│   ├── local_runtime.py       Host command execution
│   └── docker_runtime.py      Container sandbox lifecycle
├── scheduler/
│   ├── scheduler.py           Task CRUD and run dispatch
│   └── scheduler_loop.py      Background due-task poller
├── search/                    Pluggable web search providers + cache
├── calendar/                  Local and external calendar integrations
└── reports/
    └── final_report.py        Markdown report from RunContext or DB
```

## Run lifecycle

```
POST /api/runs
    │
    ▼
Insert runs row (provider, model, runtime_mode, memory_mode)
    │
    ▼
RunStateMachine.execute()
    │
    ├── for each phase in PHASE_ORDER:
    │       emit phase_started event
    │       run phase handler (may call LLM, tools, tests)
    │       emit phase_completed | phase_failed | phase_skipped | phase_blocked
    │
    ▼
final_report phase → INSERT reports
    │
    ▼
Update runs.status = completed | failed
```

Blocked phases (approval required) pause until `POST /api/runs/{id}/approve` resolves the pending action.

## Frontend layout

```
frontend/src/
├── App.tsx              Shell, routing, run/report navigation
├── api/                 REST client, SSE event merge helpers
├── pages/
│   ├── HomePage.tsx         Mission launcher (provider, runtime, task)
│   ├── RunDashboardPage.tsx Mission console + phase rail
│   ├── SchedulerPage.tsx    Quest Queue
│   ├── ReportPage.tsx       Final report viewer
│   └── …                    Memory, Search, Calendar, Settings, Safety
├── components/          Shared UI (PhaseTimeline, MissionProviderFields, …)
├── lib/                 Pure helpers (phaseState, scheduler, runId)
├── games/               Optional mini-games during long runs
└── theme/               Dark/light token system
```

The console subscribes to `GET /api/runs/{id}/stream` (SSE) and merges events into the phase timeline via `deriveMissionState()`.

## Data stores

| Store | Location | Contents |
|-------|----------|----------|
| SQLite | `patchquest.db` (configurable) | Runs, events, approvals, memory, scheduler, reports, search cache |
| Sandbox workspace | `~/.patchquest/sandboxes/{run_id}/` | Docker runtime working copy |
| Local state | `~/.patchquest/` | Calendar ICS export, runtime artifacts |

Both `patchquest.db` and `.patchquest/` are gitignored.

## Provider routing

The frontend and scheduler pass `provider` + `model` on run creation. The orchestrator resolves the provider class from `provider_registry.py` and builds a `ModelConfig` with base URL and API key env var from the provider catalog.

Mock provider returns structured JSON matching each role's schema—useful for CI and demos without network calls.

## Scheduler integration

Scheduled tasks persist `provider`, `model`, `runtime_mode`, and `memory_mode`. When due, `_execute_task()` inserts a run row and constructs `RunStateMachine` with the same parameters as manual `POST /api/runs`. Provider availability is validated before execution; missing keys produce a failed run and report rather than silent mock fallback.

## Extension points

- **New LLM provider**: implement `ProviderBase`, register in `provider_registry.py`, add catalog entry in `routes_providers.py`.
- **New search provider**: implement `SearchProviderBase`, register in `search_registry.py`.
- **New calendar provider**: implement `CalendarProviderBase`, register in `calendar_registry.py`.
- **New phase** (advanced): extend `Phase` enum, add handler in state machine, update frontend phase labels.

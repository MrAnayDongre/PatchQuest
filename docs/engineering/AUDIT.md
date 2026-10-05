# Forensic audit (code-derived)

Scope: everything under `backend/patchquest` (about 7,500 lines), tests, Docker, packaging and docs.
**Not yet audited in depth:** `frontend/src` internals, `memory/memory_store.py`, `search/`,
`calendar/`, `scheduler/` beyond startup behaviour. They are marked UNAUDITED below.

## Architecture map (as found)

```
frontend (React, SSE) --HTTP--> api/routes_*.py ---> orchestrator/state_machine.py
                                                     |-- agents/roles.py ---> providers_*.py (httpx)
                                                     |-- memory/{repo_indexer,code_graph,repo_map}.py (SQLite)
                                                     |-- tools/{patch_tools,command_runner,secret_guard,test_runner}.py
                                                     `-- runtime/{local,docker}_runtime.py   (not used by the pipeline)
scheduler/ (poll loop) ---> creates runs through the same state machine
database.py: one sqlite3 connection per call, WAL, ad-hoc _migrate()
event_bus.py: in-process asyncio queues; events also persisted in run_events
```

| Area | Where | Class |
|---|---|---|
| ENTRYPOINTS | `main.py` (uvicorn, binds 0.0.0.0 when run as `__main__`); no CLI | INCOMPLETE |
| STATE MACHINE | `orchestrator/state_machine.py`, 12 phases, in-memory only | FRAGILE (no persistence of phase state, no resume, BLOCKED does not stop the run) |
| AGENT LOOP | single-shot JSON call per role; no iteration, no repair after failing tests | UNDERDESIGNED |
| MODEL PROVIDERS | 8 classes + 1 catalogue in an API module; `_call_role` duplicated in `_call_role_text`; no retry/backoff, no capability negotiation, structured-output schemas defined but unused | DUPLICATED / UNDERDESIGNED |
| TOOL SYSTEM | plain functions; no registry, no schema, no policy hook | UNDERDESIGNED |
| PLUGINS | none | INCOMPLETE |
| REPO INDEXING | tree-sitter + regex fallback into SQLite; full re-scan each run | SOLID core, no incremental mode |
| CONTEXT | planner sees 50 file names; builder output is LLM text | **BROKEN** (no real file reading) |
| MEMORY | per-repo facts with hash invalidation | UNAUDITED (tests exist) |
| PATCH APPLICATION | was line-number replace without verification | **BROKEN** -> replaced (this branch) |
| COMMAND EXECUTION | was `shell=True`, full env, unclassified | **UNSAFE** -> replaced (this branch) |
| SANDBOX | Docker runtime with `--network=none`, memory/cpu/pid limits, secret-excluding copy | SOLID in isolation, MISPLACED (unused) |
| TEST EXECUTION | first 2 commands, result never gates anything | INCOMPLETE |
| DATABASE | SQLite, WAL, foreign keys; no schema version, sync calls inside async handlers | FRAGILE |
| API | no auth, no `repo_path` validation, `_active_machines` never cleaned, fire-and-forget task | **UNSAFE** |
| FRONTEND | builds, typechecks, 3 failing tests (env) | UNAUDITED |
| SCHEDULER / SEARCH / CALENDAR | ~2,400 lines unrelated to the core product | OVERENGINEERED for the mission |
| CI / RELEASE | none | INCOMPLETE |
| CONFIG | pydantic models + YAML; env parsing scattered | FRAGILE |

## Top 15 problems (ordered by damage)

1. Patch engine silently corrupted files (fixed).
2. Model-chosen commands ran unchecked on the host with the host environment (fixed).
3. Repo content is a direct prompt-injection-to-RCE path through `test_commands` (mitigated by 2; further work: untrusted-data framing).
4. API is unauthenticated, accepts any `repo_path`, and the `__main__` entry binds `0.0.0.0`.
5. Patches land in the real repo before any validation; no rollback.
6. No patch->test->repair loop; failing tests change nothing.
7. The coder never sees real file contents.
8. Approvals and risk gating exist only as schema/docs, never in the flow.
9. No durability: a crash leaves runs `running` forever; nothing can resume.
10. Phase contracts are implicit: any exception inside a phase fails it; BLOCKED continues.
11. Provider layer duplicates code, has no retries/timeouts policy, no capability detection.
12. No CI, lint, type checking or integration tests; frontend test suite red.
13. Mock provider never emits a diff, so the default demo path never exercises patching.
14. ~2,400 lines of calendar/search/games scope dilute the product.
15. No CLI, no SDK, no `doctor`; install path is two separate manual setups.

## Top 10 high-leverage improvements

1. Verified patch engine (done). 2. Sound execution policy and executor (done).
3. Shadow workspace + promote-on-green with baseline comparison (in progress).
4. Real, explainable, token-budgeted context from disk and the code graph.
5. Durable, event-sourced runs with resume (phase transitions in the DB, crash recovery).
6. One application service layer that CLI/API/UI all call.
7. Provider contract with capability negotiation + retry/backoff (and constrained decoding for local engines).
8. `patchquest` CLI + `doctor`.
9. Evaluation harness with a golden corpus and a scripted model, run in CI.
10. CI, lint, types, packaging.

## Priorities

- **P0 blockers:** 1, 2 (done); API auth/binding/`repo_path` validation; no unvalidated write to the real repo.
- **P1 reliability:** shadow workspace, repair loop, approvals, durable runs, real context, phase contracts.
- **P2 differentiators:** deterministic replay (scripted-model eval + event-sourced runs), evidence-backed
  context with provenance, baseline-vs-patch validation, small-model-friendly edit format and constrained decoding.
- **P3 DX/UX:** CLI, doctor, frontend run/diff/timeline views fed by the real event schema.
- **P4 OSS:** CI, docs, templates, release process, trimming off-mission modules into extras.

## Target architecture (layering rule: dependencies point inward)

```
interfaces    cli | api | web | sdk
application   TaskService (create/run/pause/resume/approve/replay/inspect) -- the only entry for interfaces
runtime       phase engine (contracts, transitions, retries, budgets), agent roles
core          Task/Run/Phase/Event/Checkpoint types (pure)
adapters      providers | execution+sandbox | patching | repo-intel | context | persistence | plugins
```

Migration is strangler-style: new modules are introduced behind the existing `RunStateMachine`
interface so the 331 existing tests keep passing, then call sites move to `application/` and the
old paths are deleted once unused.

## First vertical slice (in progress)

Task -> deterministic context from disk (with provenance) -> plan -> patch in a **shadow workspace**
-> static checks + tests in that workspace (policy-gated, scrubbed env, optional Docker) -> bounded
repair loop -> baseline comparison on failure -> promote to the real repo only if validated, with
sha256 preconditions -> report with outcome and provenance.

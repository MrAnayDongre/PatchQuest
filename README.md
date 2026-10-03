# PatchQuest

**A durable, safe runtime for long-horizon software-engineering agents.** An agent works in an isolated copy of your
repository, proves its change with your tests, and touches the real code only once - after validation, with hash checks,
journaled so a crash can never leave it half-written. Every run is a persisted, replayable, forkable record, so you can
kill the process mid-run, restart, and carry on; ask what happened and why; and swap the model at any checkpoint.

It is built to work with **small and local models** first (SGLang, vLLM, llama.cpp, Ollama, LM Studio) and any
OpenAI-compatible endpoint, and treats evaluation and recovery as product features, not afterthoughts.

> There is no screenshot in this README on purpose: the UI was rebuilt and no browser was available to capture it.
> Run it and look.

## What is real today

Status words are literal. **Implemented** = code exists. **Tested** = a test fails if it breaks.
**Measured** = numbers in [docs/benchmarks.md](docs/benchmarks.md). Test counts: 1,402 backend, 187 frontend.

| Capability | Status | Notes |
|---|---|---|
| Validated-patch pipeline: shadow workspace, tests, repair loop, baseline attribution, promote-after-validation | tested | live-model baseline is weak: see below |
| Command policy, scrubbed env, process-group kill, Docker sandbox (no network, caps dropped, read-only root) | tested incl. real containers | Docker is not VM isolation |
| Immutable event ledger, typed status transitions, versioned migrations with backup | tested | [docs/events.md](docs/events.md) |
| Checksummed checkpoints, crash-safe `resume` (repo drift, journaled promotion, rollback) | tested incl. real `SIGKILL` | [docs/failure-recovery.md](docs/failure-recovery.md) |
| Replay (state / recorded-model / live), fork from a checkpoint with another model, run lineage | tested | [docs/replay.md](docs/replay.md) |
| Failure taxonomy, one retry engine, execution budgets, cancellation that reaches subprocess trees | tested | |
| Approvals: once / for this run / deny / modify / cancel, side-effect classes, expiry | tested | [docs/approvals.md](docs/approvals.md) |
| Organisations, workspaces, roles, hashed tokens, tenant isolation, audit log | tested (isolation matrix + mutation check) | [docs/security.md](docs/security.md) |
| Durable workflows: approvals, timers, event waits, crash-safe actions, 4 templates | tested | [docs/workflows.md](docs/workflows.md) |
| Connectors: signed webhooks, SSRF-guarded outbound, grant-enforcing contract, GitHub | tested against a **simulated** GitHub only | [docs/connectors.md](docs/connectors.md) |
| Run queue + workers with leases; a killed worker's run is recovered by another | tested incl. real `SIGKILL` | single host |
| Metrics derived from the ledger; OTLP trace export | tested | [docs/operations.md](docs/operations.md) |
| Control-plane load: 1,000 orgs / 10,000 runs / 400k events | **measured** on one machine | [docs/benchmarks.md](docs/benchmarks.md) |
| Container image + compose (API and workers), backups with verify/restore | built and run here | [docs/deployment.md](docs/deployment.md) |
| Web UI: inspect any run from persisted state, approve from a phone-width screen, recover, fork, replay | typecheck + 187 tests + build; **never viewed in a browser** | |
| Agent gym (hidden-oracle episodes, decomposed rewards, redacted trajectories) | tested | [docs/rl-gym.md](docs/rl-gym.md) |

**Not built:** PostgreSQL / multi-host workers, SSO, a plugin loader, a drag-and-drop workflow builder, Slack/Jira/Linear/Notion
connectors, a branch-push action (so no automatic pull requests), vector search, per-tenant quotas. Nothing in this repo has
been validated for "thousands of teams"; the measured ceiling of one host is in the benchmarks.

## Honest model results

On the bundled 14-task evaluation corpus with **Qwen3-0.6B** served locally by SGLang the success rate is **0 of 14** (best
earlier run 1 of 14). Harness work (reasoning-output stripping, context budgeting, apply-feedback, indentation-tolerant
edits) cut time and tokens about 4x; the remaining failures are mostly the model inventing search text that is not in the
file. A 0.6B model is the floor, not the target: the point of the corpus is to measure a better model and to attribute each
failure to the model, the harness or the environment. Results are in `docs/evals/`.

## Quick start

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
patchquest doctor                          # checks the installation and safety boundaries
patchquest run --repo ~/code/app --task "Fix add() so it returns the sum" --provider mock          # no model needed
patchquest run --repo ~/code/app --task "..." --provider sglang --model Qwen/Qwen3-0.6B --base-url http://localhost:30000/v1
```
`mock`/`scripted` providers are deterministic fixtures. `patchquest engines` shows which local engines are running.

```bash
patchquest status RUN | inspect RUN | events RUN | checkpoints RUN | diff RUN | report RUN
patchquest resume RUN [--plan]             # after a crash; explains first, asks before anything uncertain
patchquest fork RUN --from 6 --model other --set agent.max_model_calls=80
patchquest replay RUN --mode state|model|live
patchquest metrics --window 7d --by model  |  patchquest trace RUN   |  patchquest queue  |  patchquest worker
patchquest workflows templates|save|start|decide ...        patchquest admin init|token ...      patchquest backup create|verify|restore
patchquest eval run --provider scripted    # the evaluation corpus; `eval compare A.json B.json` flags regressions
```
Exit codes: `0` ok, `1` failed, `2` patch rejected, `3` interrupted, `4` a person must decide first, `64` usage.

UI: `cd frontend && npm ci && npm run build`, then `PATCHQUEST_STATIC_DIR=frontend/dist patchquest serve` (or `npm run dev` with the API on :8000).

Teams on one host: `docker compose run --rm api patchquest admin init ...` then `docker compose up -d --scale worker=3`
([deployment](docs/deployment.md)).

## How a run works

`created -> running -> completed | failed | cancelled` (plus `queued`, `waiting_approval`, `interrupted`). Twelve phases; the
change is made in a shadow copy, validated, repaired within a budget, reviewed, security-scanned, and only then promoted
by policy (or left as a diff for a person). After every phase a checksummed checkpoint is written. Details:
[docs/runtime.md](docs/runtime.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Configuration

`config.yaml` (see `sample.config.yaml`) and environment variables: `PATCHQUEST_DB`, `_HOST`, `_PORT`, `_QUEUE_MODE`,
`_WORKER_LEASE_SECONDS`, `_STATIC_DIR`, `PATCHQUEST_API_TOKEN`. Provider keys are read from the environment variable named in
the config, never stored. Per-run overrides are limited to `agent.*` (safety policy cannot be overridden).

## Safety in one paragraph

Command risk is decided deterministically from the parsed argv (blocked / automatic / needs a person), commands run with a
scrubbed environment and in their own process group, secrets are redacted before anything is stored, repository paths are
restricted, the API is loopback-only and token-protected, tenants are isolated by workspace, and every external write
needs a recorded human approval. The full model, including what it does **not** guarantee, is in
[docs/security.md](docs/security.md).

## Repository layout

`backend/patchquest/` (see the package table in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)), `backend/tests/{unit,integration,e2e,security}`,
`backend/benchmarks/`, `frontend/src/{design,features,lib,api}`, `docker/sandbox/`, `Dockerfile`, `docker-compose.yml`,
`docs/` (runtime, events, checkpoints, failure-recovery, replay, approvals, security, workflows, connectors, rl-gym,
deployment, operations, benchmarks, ADRs, evaluations, engineering audit).

## Tests

```bash
cd backend && ruff check . && mypy patchquest && pytest -q            # 1,402 tests; see backend/tests/README.md
cd frontend && npm run typecheck && npm test && npm run build         # 187 tests
```

## Contributing and security

[CONTRIBUTING.md](CONTRIBUTING.md) - [SECURITY.md](SECURITY.md) (reporting, and the pointer to the threat model) - [MIT License](LICENSE)

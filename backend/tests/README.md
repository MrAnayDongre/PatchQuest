# Testing architecture

```
tests/
├── conftest.py        one autouse fixture: fresh DB, default config, isolated workspace, clean registries
├── support/           shared helpers (runs/events, calc repo, scripted-run driver, fake model endpoint)
├── fixtures/          sample source files used by symbol-extraction tests
├── unit/              fast, deterministic, no network, no Docker, no model calls
│   ├── runtime/       state machine, phase lifecycle, orchestration contracts, reports, sandbox config
│   ├── providers/     provider contracts (capabilities, retries, budgets, recording) and adapters
│   ├── security/      command policy, secrets, path containment
│   ├── patching/      patch engine and failure attribution
│   ├── repo/          indexing, code graph, symbol extraction
│   ├── application/   TaskService
│   └── contrib/       scheduler, calendar, search (outside the core coding-agent path)
├── integration/       real components together: executor + subprocesses, HTTP API, CLI, real containers
└── e2e/
    ├── workflows/     full pipeline against real repos with a scripted model; the evaluation lab
    └── recovery/      crash / resume / failure-injection scenarios
```

## Commands

```bash
pytest tests/unit                  # ~10 s
pytest tests/integration           # ~20 s (container tests skip without Docker + the sandbox image)
pytest tests/e2e                   # ~12 s
pytest tests/unit/runtime          # one subsystem
pytest -m docker                   # only the real-container tests
cd ../frontend && npm test         # features/ and components/
```

## Where does a new test go?

* Pure logic or one component with fakes: `unit/<subsystem>/`.
* Several real components, a real subprocess, HTTP or a container: `integration/`.
* A whole task through the pipeline (or crash/resume of one): `e2e/`.
* A regression belongs in the module of the subsystem it broke, named after the **invariant**
  (`test_resume_does_not_repeat_completed_side_effect`), not after an issue number.

## Rules

* Tests never use real host directories (`/tmp`, `~`) as repositories; use `tmp_path` or `support.TEST_REPO`.
* No test talks to a real model. Use `ScriptedProvider` (deterministic) or `support.fake_server` (HTTP shape).
* Don't re-create DB/config setup: the global fixture already did.
* A test must be able to fail: prefer asserting observable behaviour over implementation details.

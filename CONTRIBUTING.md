# Contributing to PatchQuest

Thanks for helping. PatchQuest is a runtime: its value is that the hard parts (recovery, safety, side effects, evidence) stay
correct. Changes are welcome where they keep those guarantees, come with a test, and stay small enough to review.

If you are new, start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (ten minutes), then [docs/runtime.md](docs/runtime.md)
if your change touches runs, checkpoints or recovery.

## Set up

Requirements: Python 3.11 or 3.12, git, Node 20 (only for the UI), Docker (only for the sandbox tests), PostgreSQL 16 (only to run the suite in server mode).

```bash
git clone https://github.com/MrAnayDongre/PatchQuest.git && cd PatchQuest

# backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,tree-sitter]"          # add ,server for PostgreSQL and the security tests (stored secrets need cryptography)

# frontend (separate shell)
cd frontend && npm ci
```

Try the product before you change it: `patchquest demo` (see the [README quickstart](README.md#quickstart)).

## Checks that must pass

```bash
# backend (from backend/)
ruff check .
mypy
pytest tests/unit -q
pytest tests/integration -q -m "not docker"
pytest tests/e2e -q
pytest tests/security tests/unit/connectors -q
pytest tests -q -m "not docker"                      # everything, SQLite

# server mode: the same suite on PostgreSQL
PATCHQUEST_TEST_PG=postgresql://user@host/db pytest tests -q -m "not docker"

# real containers (needs Docker and the sandbox image)
docker build -t patchquest-sandbox:latest ../docker/sandbox && pytest -m docker -q

# frontend (from frontend/)
npm run typecheck
npm test
npm run build
npm run smoke -- http://127.0.0.1:8765               # browser walk-through of a running `patchquest demo`
```

These are the same gates CI runs (`.github/workflows/ci.yml`). `git diff --check` should be clean.

## Where things go

| Area | Path |
|---|---|
| Domain rules (pure, no I/O): policy, memory, tenancy, effects | `backend/patchquest/domain/` |
| Storage: ledger, checkpoints, schema and migrations | `backend/patchquest/persistence/` (SQLite and PostgreSQL share one SQL subset; `dbpg.py` translates) |
| Run state machine and phases | `backend/patchquest/orchestrator/`, `backend/patchquest/runtime/` |
| HTTP API | `backend/patchquest/api/` (every route is tenant-checked; add a test in `tests/security` for new route families) |
| Model providers | `backend/patchquest/agents/providers_*.py`, `provider_registry.py`, `providers/catalog.py` |
| Connectors and integrations | `backend/patchquest/connectors/`, `integrations/` |
| Workflows | `backend/patchquest/workflows/` |
| Plugins | `backend/patchquest/plugins/` |
| Evaluation and the agent gym | `backend/patchquest/evaluation/`, `backend/patchquest/rl/` |
| UI | `frontend/src/features/`, shared types in `frontend/src/api/` |
| Docs | `docs/` (decisions in `docs/adr/`) |

## Tests

Tests are organised by layer (`unit`, `integration`, `e2e`, `security`, `server`); [backend/tests/README.md](backend/tests/README.md)
explains where a new test belongs. Name a regression test after the invariant it protects
(`test_resume_does_not_repeat_completed_side_effect`), not after an issue number. A bug fix should come with a test that
fails without the fix. Anything that decides whether data leaves the machine, whether a command runs, or whether a side
effect is repeated needs a test that fails when the safeguard is removed.

## Extending PatchQuest

- **A model provider**: implement the provider interface in `agents/provider_base.py`, register it in `provider_registry.py`
  and `providers/catalog.py`, declare its capabilities honestly, add contract tests like those in `tests/unit/providers/`.
  See [docs/providers.md](docs/providers.md).
- **A plugin**: a manifest plus an entry point or an `external_process` command; permissions are declarations the operator
  must accept. See [docs/plugins.md](docs/plugins.md) for the trust levels (a plugin is *not* sandboxed unless you say so).
- **A connector**: implement `Connector` from `connectors/base.py`; actions are frozen models, writes need approval, and the
  first tests run against the simulator in `connectors/testing/`. Say plainly that it is simulator-tested until it has been
  run against the live service. See [docs/connectors.md](docs/connectors.md) and [docs/integrations.md](docs/integrations.md).
- **A workflow action or trigger**: see [docs/workflows.md](docs/workflows.md).

## Pull requests

1. Branch from `main`; keep the change focused.
2. Explain what changed and why in the PR (the template asks for testing, side effects and evidence).
3. Security-relevant code (`secret_guard`, command policy, policy engine, tenancy, anything that sends data out) gets extra
   scrutiny; say what could go wrong.
4. For a larger design, open an issue first. Decisions worth keeping are written down as short ADRs in `docs/adr/`.
5. Never commit secrets, `.env` files or a `config.yaml` with credentials (use `sample.config.yaml`). Test fixtures use
   obviously fake keys.

## Good places to start

Documentation fixes and examples, missing tests for edge cases, UI accessibility and keyboard behaviour, small developer
tooling, reproducing a bug with a failing test, small improvements to a simulator-backed connector. Changes to the run state
machine, leases, checkpoints, recovery or the policy engine are welcome too, but expect a careful review and a request for tests
that kill processes.

## Questions and conduct

Use GitHub Discussions for questions and ideas, Issues for bugs. Vulnerabilities go through [SECURITY.md](SECURITY.md), not a
public issue. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are licensed under the project's
[MIT license](LICENSE).

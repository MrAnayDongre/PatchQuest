# Quickstart

Three ways in, from least to most setup. Everything runs on a laptop; no GPU is needed for any of it.

## 1. See it work (two minutes, no API keys)

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
cd ../frontend && npm ci && npm run build && cd ../backend     # the UI is optional
patchquest demo                 # http://127.0.0.1:8765 - seeded runs, a waiting approval, a workflow, simulated GitHub/Slack
patchquest demo trigger         # in another terminal: a signed GitHub webhook starts a real agent run (see docs/demo.md)
patchquest demo crash           # kill a worker with SIGKILL mid-run and watch another finish it
```
The demo uses its own directory (`~/.patchquest/demo`), scripted models and simulated services; `patchquest demo reset` wipes only that.

## 2. Use it on your repository (local mode)

```bash
patchquest doctor                                   # what works on this machine, and what to fix
patchquest run --repo . --task "Fix add() so it returns the sum" --provider ollama --model qwen2.5-coder:7b
patchquest serve                                    # API + UI on 127.0.0.1; SQLite in ~/.patchquest
```
Provider setup (Ollama, vLLM, SGLang, llama.cpp, LM Studio, OpenAI-compatible, hosted APIs): [providers](providers.md). Runs are validated in a
shadow workspace and only promoted after your repository's own tests pass; risky commands and patches that did not validate wait for your approval.
`patchquest resume|replay|fork|inspect|events|metrics|explain RUN` work on any run.

## 3. Run it for a team (server mode)

```bash
pip install -e 'backend[server]'        # PostgreSQL driver + encryption
export PATCHQUEST_DATABASE_URL=postgresql://patchquest:...@db:5432/patchquest
export PATCHQUEST_SECRET_KEY=$(patchquest secrets keygen)
patchquest admin init --org Acme --workspace main --owner you      # prints a token once
patchquest serve --host 0.0.0.0 &  patchquest worker               # any number of workers, on any hosts that reach the database
```
or `docker compose -f docker-compose.server.yml up -d --scale worker=3`. Then register repositories, connect integrations, set policy:
[deployment](deployment.md), [tenancy](tenancy.md), [integrations](integrations.md), [policy](policy.md).

## Where to go next

[architecture](ARCHITECTURE.md) - [runtime and recovery](runtime.md) - [evaluation](evaluation.md) - [security](security.md) - [operations](operations.md) - [benchmarks](benchmarks.md)

# PatchQuest Backend

FastAPI application for the PatchQuest agentic coding harness.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional extras:

```bash
pip install -e ".[tree-sitter]"      # AST symbol extraction
pip install -e ".[caldav]"           # CalDAV calendar
pip install -e ".[google-calendar]"  # Google Calendar API
```

## Run

```bash
uvicorn patchquest.main:app --reload --port 8000
```

Entry point: `patchquest/main.py`

Configuration: copy `../sample.config.yaml` to `config.yaml` or set `PATCHQUEST_CONFIG`.

## Test

```bash
python -m pytest
```

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/providers` | List LLM providers |
| GET | `/api/providers/status` | Provider key availability |
| POST | `/api/runs` | Create run |
| GET | `/api/runs` | List runs |
| GET | `/api/runs/{id}` | Run details |
| GET | `/api/runs/{id}/events` | Event history |
| GET | `/api/runs/{id}/stream` | SSE event stream |
| POST | `/api/runs/{id}/approve` | Approve/reject pending action |
| GET | `/api/reports/{id}` | Final report |
| GET | `/api/memory` | Memory records |
| GET | `/api/repo-map` | Indexed repo structure |
| GET | `/api/settings` | Settings |
| POST | `/api/settings` | Update settings |
| GET | `/api/runtime/status` | Local/Docker runtime availability |
| GET | `/api/scheduler/tasks` | List scheduled tasks |
| POST | `/api/scheduler/tasks` | Create scheduled task |
| POST | `/api/scheduler/tasks/{id}/run-now` | Execute task immediately |
| GET | `/api/search/providers` | Search provider list |
| POST | `/api/search/query` | Execute web search |
| GET | `/api/calendar/events` | List calendar events |

## Database

SQLite database path defaults to `patchquest.db` (see `db_path` in config). Created automatically on startup via `patchquest/database.py`.

Core tables: `runs`, `run_events`, `approvals`, `memory_records`, `repo_files`, `repo_symbols`, `scheduled_tasks`, `scheduled_run_history`, `reports`, `search_cache`, `calendar_events`.

## Module map

See [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for orchestrator, agents, tools, and runtime design.

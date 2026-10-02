"""PatchQuest backend - FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from patchquest.api.routes_calendar import router as calendar_router
from patchquest.api.routes_memory import router as memory_router
from patchquest.api.routes_providers import router as providers_router
from patchquest.api.routes_repo import router as repo_router
from patchquest.api.routes_reports import router as reports_router
from patchquest.api.routes_runs import router as runs_router
from patchquest.api.routes_runtime import router as runtime_router
from patchquest.api.routes_scheduler import router as scheduler_router
from patchquest.api.routes_search import router as search_router
from patchquest.api.routes_settings import router as settings_router
from patchquest.api.schemas import HealthResponse
from patchquest.config import get_config
from patchquest.database import init_db
from patchquest.logging_config import setup_logging
from patchquest.recovery import recover_interrupted_runs
from patchquest.security import allowed_hosts, check_startup_policy, require_auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    check_startup_policy(get_config().host)
    init_db()
    recover_interrupted_runs()

    from patchquest.scheduler.scheduler_loop import start_scheduler_loop, stop_scheduler_loop
    await start_scheduler_loop(poll_interval=30)

    yield

    await stop_scheduler_loop()


app = FastAPI(
    title="PatchQuest",
    description="Local-first coding-agent harness for tiny/SLM models",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_auth)],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs_router)
app.include_router(providers_router)
app.include_router(settings_router)
app.include_router(memory_router)
app.include_router(repo_router)
app.include_router(reports_router)
app.include_router(runtime_router)
app.include_router(scheduler_router)
app.include_router(search_router)
app.include_router(calendar_router)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


if __name__ == "__main__":
    import uvicorn
    cfg = get_config()
    check_startup_policy(cfg.host)
    uvicorn.run("patchquest.main:app", host=cfg.host, port=cfg.port)

"""Run management API routes."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from patchquest.api.schemas import (
    ApprovalAction,
    CreateRunRequest,
    RunEventResponse,
    RunResponse,
)
from patchquest.database import get_db, insert_event, now_iso
from patchquest.orchestrator.event_bus import event_bus
from patchquest.orchestrator.state_machine import RunStateMachine

router = APIRouter(prefix="/api/runs", tags=["runs"])

_active_machines: dict[str, RunStateMachine] = {}


def _row_to_response(r: Any) -> RunResponse:
    """Convert a sqlite3.Row to RunResponse, handling missing columns from old DBs."""
    keys = r.keys() if hasattr(r, "keys") else []
    return RunResponse(
        id=r["id"],
        repo_path=r["repo_path"],
        task=r["task"],
        status=r["status"],
        current_phase=r["current_phase"],
        provider=r["provider"] if "provider" in keys else "mock",
        model=r["model"] if "model" in keys else None,
        runtime_mode=r["runtime_mode"] if "runtime_mode" in keys else "local",
        model_profile=r["model_profile"],
        memory_mode=r["memory_mode"],
        allow_network=bool(r["allow_network"]),
        dry_run=bool(r["dry_run"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        completed_at=r["completed_at"],
    )


@router.post("", response_model=RunResponse)
async def create_run(req: CreateRunRequest) -> RunResponse:
    run_id = str(uuid.uuid4())
    now = now_iso()

    provider = req.provider or "mock"
    model = req.model
    runtime_mode = req.runtime_mode or "local"

    with get_db() as conn:
        conn.execute(
            """INSERT INTO runs (id, repo_path, task, status, provider, model, model_profile,
               memory_mode, runtime_mode, allow_network, dry_run, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, req.repo_path, req.task, "created", provider, model,
             req.model_profile, req.memory_mode or "repo", runtime_mode,
             int(req.allow_network), int(req.dry_run), now, now),
        )
        insert_event(conn, run_id, "run_created", message=f"Run created: {req.task[:100]}")

    machine = RunStateMachine(
        run_id, req.repo_path, req.task,
        provider=provider, model=model, runtime_mode=runtime_mode,
        dry_run=req.dry_run,
    )
    _active_machines[run_id] = machine
    asyncio.get_event_loop().create_task(machine.execute())

    return RunResponse(
        id=run_id,
        repo_path=req.repo_path,
        task=req.task,
        status="created",
        provider=provider,
        model=model,
        runtime_mode=runtime_mode,
        model_profile=req.model_profile,
        memory_mode=req.memory_mode or "repo",
        allow_network=req.allow_network,
        dry_run=req.dry_run,
        created_at=now,
        updated_at=now,
    )


@router.get("", response_model=list[RunResponse])
async def list_runs() -> list[RunResponse]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 50").fetchall()

    return [_row_to_response(r) for r in rows]


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: str) -> RunResponse:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Run not found")
    return _row_to_response(r)


@router.get("/{run_id}/events", response_model=list[RunEventResponse])
async def get_events(run_id: str) -> list[RunEventResponse]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()

    return [
        RunEventResponse(
            id=r["id"],
            run_id=r["run_id"],
            type=r["type"],
            phase=r["phase"],
            status=r["status"],
            message=r["message"],
            payload=json.loads(r["payload_json"]) if r["payload_json"] else None,
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get("/{run_id}/stream")
async def stream_events(run_id: str) -> EventSourceResponse:
    async def generate():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        event_bus.subscribe(run_id, queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"event": event["type"], "data": json.dumps(event)}
                    if event["type"] in ("run_completed", "run_failed"):
                        break
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            event_bus.unsubscribe(run_id, queue)

    return EventSourceResponse(generate())


@router.post("/{run_id}/approve")
async def approve_action(run_id: str, action: ApprovalAction) -> dict[str, str]:
    with get_db() as conn:
        conn.execute(
            "UPDATE approvals SET status = ?, note = ?, resolved_at = ? WHERE id = ? AND run_id = ?",
            ("approved" if action.approved else "rejected", action.note, now_iso(),
             action.approval_id, run_id),
        )
        event_type = "permission_approved" if action.approved else "permission_rejected"
        insert_event(conn, run_id, event_type, message=f"Approval {action.approval_id}: {action.approved}")

    machine = _active_machines.get(run_id)
    if machine:
        await machine.resolve_approval(action.approval_id, action.approved)

    return {"status": "ok"}


@router.post("/{run_id}/reject")
async def reject_action(run_id: str, action: ApprovalAction) -> dict[str, str]:
    action.approved = False
    return await approve_action(run_id, action)

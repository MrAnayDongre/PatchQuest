"""Run management API routes (thin adapter over :class:`TaskService`)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from patchquest.api.schemas import ApprovalAction, CreateRunRequest, RunEventResponse, RunResponse
from patchquest.application import get_service
from patchquest.application.service import RunNotActive, RunNotFound
from patchquest.security import RepoPathError

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _to_response(r: dict[str, Any]) -> RunResponse:
    return RunResponse(
        id=r["id"], repo_path=r["repo_path"], task=r["task"], status=r["status"],
        current_phase=r.get("current_phase"), provider=r.get("provider") or "mock", model=r.get("model"),
        runtime_mode=r.get("runtime_mode") or "local", model_profile=r.get("model_profile"),
        memory_mode=r.get("memory_mode"), allow_network=bool(r.get("allow_network")),
        dry_run=bool(r.get("dry_run")), created_at=r["created_at"], updated_at=r["updated_at"],
        completed_at=r.get("completed_at"),
    )


@router.post("", response_model=RunResponse)
async def create_run(req: CreateRunRequest) -> RunResponse:
    service = get_service()
    try:
        run = service.create_run(
            repo_path=req.repo_path, task=req.task, provider=req.provider or "mock", model=req.model,
            runtime_mode=req.runtime_mode or "local", model_profile=req.model_profile,
            memory_mode=req.memory_mode or "repo", allow_network=req.allow_network, dry_run=req.dry_run,
            base_url=req.base_url,
        )
    except (RepoPathError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    service.launch(run["id"])
    return _to_response(run)


@router.get("", response_model=list[RunResponse])
async def list_runs() -> list[RunResponse]:
    return [_to_response(r) for r in get_service().list_runs(50)]


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: str) -> RunResponse:
    try:
        return _to_response(get_service().get_run(run_id))
    except RunNotFound:
        raise HTTPException(404, "Run not found") from None


@router.get("/{run_id}/events", response_model=list[RunEventResponse])
async def get_events(run_id: str, after_id: int = Query(0, ge=0)) -> list[RunEventResponse]:
    try:
        events = get_service().events(run_id, after_id)
    except RunNotFound:
        raise HTTPException(404, "Run not found") from None
    return [RunEventResponse(id=e["id"], run_id=e["run_id"], type=e["type"], phase=e["phase"], status=e["status"],
                             message=e["message"], payload=e["payload"], created_at=e["created_at"]) for e in events]


@router.get("/{run_id}/stream")
async def stream_events(run_id: str, after_id: int = Query(0, ge=0)) -> EventSourceResponse:
    service = get_service()
    try:
        service.get_run(run_id)
    except RunNotFound:
        raise HTTPException(404, "Run not found") from None

    async def generate():
        async for event in service.stream(run_id, after_id):
            yield {"event": event["type"], "id": str(event.get("id", "")), "data": json.dumps(event)}

    return EventSourceResponse(generate())


@router.post("/{run_id}/approve")
async def approve_action(run_id: str, action: ApprovalAction) -> dict[str, str]:
    await get_service().approve(run_id, action.approval_id, action.approved, action.note)
    return {"status": "ok"}


@router.post("/{run_id}/reject")
async def reject_action(run_id: str, action: ApprovalAction) -> dict[str, str]:
    action.approved = False
    return await approve_action(run_id, action)


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, str]:
    try:
        get_service().cancel(run_id)
    except RunNotActive:
        raise HTTPException(409, "Run is not active") from None
    return {"status": "cancelling"}

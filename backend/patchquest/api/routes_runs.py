"""Run management API routes (thin adapter over :class:`TaskService`).

Authorisation lives in dependencies (``patchquest.api.auth``): every handler states the permission it needs,
and run-scoped handlers receive a ``RunAccess`` that has already been checked against the run's workspace.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from patchquest.api.auth import CurrentPrincipal, RunAccess, record, run_access
from patchquest.api.schemas import (
    ApprovalAction,
    ApprovalDecision,
    CreateRunRequest,
    ForkRequest,
    ReplayRequest,
    ResumeRequest,
    RunEventResponse,
    RunResponse,
)
from patchquest.application import get_service
from patchquest.application.service import ForkBlocked, ForkError, RunNotActive
from patchquest.config import get_config
from patchquest.domain.approvals import ApprovalError, Decision
from patchquest.domain.identity import Permission
from patchquest.persistence import checkpoints
from patchquest.runtime.replay import NotReplayable, ReplayMode, StateReplay, comparison_payload
from patchquest.runtime.resume import ConfirmationRequired, NotResumable
from patchquest.security import RepoPathError

router = APIRouter(prefix="/api/runs", tags=["runs"])

CanRead = Annotated[RunAccess, Depends(run_access(Permission.RUN_READ))]
CanControl = Annotated[RunAccess, Depends(run_access(Permission.RUN_CONTROL))]
CanDecide = Annotated[RunAccess, Depends(run_access(Permission.APPROVAL_DECIDE))]


def _to_response(r: dict[str, Any]) -> RunResponse:
    return RunResponse(
        id=r["id"], repo_path=r["repo_path"], task=r["task"], status=r["status"],
        current_phase=r.get("current_phase"), provider=r.get("provider") or "mock", model=r.get("model"),
        runtime_mode=r.get("runtime_mode") or "local", model_profile=r.get("model_profile"),
        memory_mode=r.get("memory_mode"), allow_network=bool(r.get("allow_network")),
        dry_run=bool(r.get("dry_run")), created_at=r["created_at"], updated_at=r["updated_at"],
        completed_at=r.get("completed_at"), workspace_id=r.get("workspace_id") or "ws_local", outcome=r.get("outcome"),
        verdict=r.get("verdict"), failure_kind=r.get("failure_kind"), attempt=r.get("attempt") or 1,
        parent_run_id=r.get("parent_run_id"), lineage_kind=r.get("lineage_kind"),
    )


def _create_workspace(principal: Any, requested: str | None) -> str:
    allowed = principal.workspaces_with(Permission.RUN_CREATE)
    if requested:
        if requested not in allowed:
            raise HTTPException(403, {"code": "forbidden", "message": "run.create is not permitted in that workspace"})
        return requested
    if len(allowed) == 1:
        return allowed[0]
    if not allowed:
        raise HTTPException(403, {"code": "forbidden", "message": "run.create is not permitted"})
    raise HTTPException(422, {"code": "workspace_required", "message": "choose a workspace_id", "workspaces": allowed})


@router.post("", response_model=RunResponse)
async def create_run(req: CreateRunRequest, request: Request, principal: CurrentPrincipal) -> RunResponse:
    workspace_id = _create_workspace(principal, req.workspace_id)
    service = get_service()
    try:
        run = service.create_run(
            repo_path=req.repo_path, task=req.task, provider=req.provider or "mock", model=req.model,
            runtime_mode=req.runtime_mode or "local", model_profile=req.model_profile,
            memory_mode=req.memory_mode or "repo", allow_network=req.allow_network, dry_run=req.dry_run,
            base_url=req.base_url, workspace_id=workspace_id, created_by=principal.actor, overrides=req.overrides or None,
        )
    except (RepoPathError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    record(request, principal, "run.create", workspace_id, run["id"], detail={"provider": run["provider"], "model": run["model"]})
    if get_config().queue_mode:
        service.enqueue(run["id"], actor=principal.actor)  # a worker will claim it
        run = service.get_run(run["id"])
    else:
        service.launch(run["id"])
    return _to_response(run)


@router.get("", response_model=list[RunResponse])
async def list_runs(principal: CurrentPrincipal, limit: int = Query(50, ge=1, le=200),
                    before: str | None = Query(None, description="created_at of the last run seen, to page backwards")) -> list[RunResponse]:
    return [_to_response(r) for r in get_service().list_runs(limit, principal.workspaces_with(Permission.RUN_READ), before)]


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(access: CanRead) -> RunResponse:
    return _to_response(access.run)


@router.get("/{run_id}/events", response_model=list[RunEventResponse])
async def get_events(access: CanRead, after_id: int = Query(0, ge=0)) -> list[RunEventResponse]:
    events = get_service().events(access.run["id"], after_id)
    return [RunEventResponse(id=e["id"], run_id=e["run_id"], type=e["type"], phase=e["phase"], status=e["status"],
                             message=e["message"], payload=e["payload"], created_at=e["created_at"]) for e in events]


@router.get("/{run_id}/stream")
async def stream_events(access: CanRead, after_id: int = Query(0, ge=0)) -> EventSourceResponse:
    service = get_service()

    async def generate():
        async for event in service.stream(access.run["id"], after_id):
            # Unnamed events: a client receives every type through ``onmessage`` without listing them, and
            # the ``id`` lets EventSource resume from where it dropped (``Last-Event-ID``).
            yield {"id": str(event.get("id", "")), "data": json.dumps(event)}

    return EventSourceResponse(generate())


_APPROVAL_STATUS = {"approval_not_found": 404, "approval_already_decided": 409, "decision_not_allowed": 422}


async def _decide(request: Request, access: RunAccess, approval_id: str, body: ApprovalDecision) -> dict[str, Any]:
    run_id = access.run["id"]
    try:
        outcome = await get_service().decide(run_id, approval_id, body.decision, actor=access.principal.actor,
                                             note=body.note, modified_command=body.modified_command)
    except ApprovalError as exc:
        record(request, access.principal, "approval.decide", access.workspace_id, run_id, outcome="refused",
               detail={"approval_id": approval_id, "decision": body.decision.value, "code": exc.code})
        raise HTTPException(_APPROVAL_STATUS.get(exc.code, 400), {"code": exc.code, "message": str(exc)}) from None
    record(request, access.principal, "approval.decide", access.workspace_id, run_id,
           detail={"approval_id": approval_id, "decision": outcome.decision.value})
    return {"status": outcome.status.value, "decision": outcome.decision.value}


@router.get("/{run_id}/approvals")
async def pending_approvals(access: CanRead) -> list[dict[str, Any]]:
    return get_service().pending_approvals(access.run["id"])


@router.post("/{run_id}/approvals/{approval_id}")
async def decide_approval(approval_id: str, body: ApprovalDecision, request: Request, access: CanDecide) -> dict[str, Any]:
    return await _decide(request, access, approval_id, body)


@router.post("/{run_id}/approve")
async def approve_action(action: ApprovalAction, request: Request, access: CanDecide) -> dict[str, Any]:
    decision = Decision.APPROVE_ONCE if action.approved else Decision.DENY
    return await _decide(request, access, action.approval_id, ApprovalDecision(decision=decision, note=action.note))


@router.post("/{run_id}/reject")
async def reject_action(action: ApprovalAction, request: Request, access: CanDecide) -> dict[str, Any]:
    return await _decide(request, access, action.approval_id, ApprovalDecision(decision=Decision.DENY, note=action.note))


@router.post("/{run_id}/cancel")
async def cancel_run(request: Request, access: CanControl) -> dict[str, str]:
    try:
        get_service().cancel(access.run["id"])
    except RunNotActive:
        raise HTTPException(409, "Run is not active") from None
    record(request, access.principal, "run.cancel", access.workspace_id, access.run["id"])
    return {"status": "cancelling"}


# ------------------------------------------------------------------ durability
def _plan_detail(exc: NotResumable) -> dict[str, Any]:
    return {"code": "confirmation_required" if isinstance(exc, ConfirmationRequired) else "not_resumable",
            "message": str(exc), "plan": exc.plan.explain()}


@router.get("/{run_id}/resume-plan")
async def resume_plan(access: CanRead) -> dict[str, Any]:
    from patchquest.runtime.resume import plan_resume

    return plan_resume(access.run["id"]).explain()


@router.post("/{run_id}/resume")
async def resume_run(body: ResumeRequest, request: Request, access: CanControl) -> dict[str, Any]:
    try:
        plan = get_service().resume(access.run["id"], accept_drift=body.accept_drift, rollback=body.rollback,
                                    defer=get_config().queue_mode)
    except NotResumable as exc:  # includes ConfirmationRequired
        raise HTTPException(409, _plan_detail(exc)) from None
    record(request, access.principal, "run.resume", access.workspace_id, access.run["id"],
           detail={"category": plan.category.value, "accept_drift": body.accept_drift, "rollback": body.rollback})
    return plan.explain()


@router.post("/{run_id}/fork", response_model=RunResponse)
async def fork_run(body: ForkRequest, request: Request, access: CanControl) -> RunResponse:
    principal = access.principal
    if not principal.can(Permission.RUN_CREATE, access.workspace_id):
        raise HTTPException(403, {"code": "forbidden", "message": "run.create is not permitted in this workspace"})
    try:
        child = get_service().fork(access.run["id"], from_seq=body.from_checkpoint, provider=body.provider, model=body.model,
                                   base_url=body.base_url, overrides=body.overrides, accept_drift=body.accept_drift,
                                   defer=get_config().queue_mode)
    except ForkBlocked as exc:
        raise HTTPException(409, {"code": "confirmation_required", "message": str(exc), "drift": exc.drift.kind.value}) from None
    except (ForkError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None
    record(request, principal, "run.fork", access.workspace_id, child["id"], detail={"parent": access.run["id"]})
    return _to_response(child)


@router.post("/{run_id}/replay")
async def replay_run(body: ReplayRequest, request: Request, access: CanControl) -> dict[str, Any]:
    try:
        result = get_service().replay(access.run["id"], ReplayMode(body.mode))
    except NotReplayable as exc:
        raise HTTPException(409, {"code": "not_replayable", "message": str(exc)}) from None
    record(request, access.principal, "run.replay", access.workspace_id, access.run["id"], detail={"mode": body.mode})
    if isinstance(result, StateReplay):
        return {"mode": "state", "ok": result.ok, "findings": list(result.findings), "status_trail": list(result.status_trail),
                "phases": result.phases, "events": result.events, "checkpoints": result.checkpoints}
    return {"mode": body.mode, "run": _to_response(result).model_dump()}


@router.get("/{run_id}/replay/{replay_id}/comparison")
async def replay_comparison(replay_id: str, access: CanRead) -> dict[str, Any]:
    from patchquest.runtime.replay import compare_runs

    child = get_service().get_run(replay_id)
    if child.get("parent_run_id") != access.run["id"] or child.get("workspace_id") != access.workspace_id:
        raise HTTPException(404, "Replay not found")
    return comparison_payload(compare_runs(access.run["id"], replay_id))


@router.get("/{run_id}/lineage")
async def run_lineage(access: CanRead) -> dict[str, Any]:
    return get_service().lineage(access.run["id"])


@router.get("/{run_id}/checkpoints")
async def run_checkpoints(access: CanRead) -> list[dict[str, Any]]:
    from patchquest.database import get_db

    with get_db() as conn:
        return checkpoints.describe(conn, access.run["id"])


@router.get("/{run_id}/budget")
async def run_budget(access: CanRead) -> list[dict[str, Any]]:
    return get_service().budget(access.run["id"])

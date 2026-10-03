"""Workflow API: definitions (versioned), runs, approvals and waits.

Every object belongs to a workspace; a workflow or run you cannot read is reported as not found.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from patchquest.api.auth import CurrentPrincipal, record
from patchquest.database import get_db
from patchquest.domain.identity import Permission, Principal
from patchquest.domain.workflows import DefinitionError, Problem, parse, to_dict
from patchquest.workflows import store
from patchquest.workflows.engine import WorkflowError
from patchquest.workflows.runtime import get_engine
from patchquest.workflows.templates import TEMPLATES, requires

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class DefinitionBody(BaseModel):
    definition: dict[str, Any]
    workspace_id: str | None = None


class StartBody(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class DecisionBody(BaseModel):
    decision: str


class ResolveBody(BaseModel):
    outcome: str


def _problems(items: list[Problem]) -> list[dict[str, Any]]:
    return [{"code": p.code, "message": p.message, "node": p.node} for p in items]


def _authorize(request: Request, principal: Principal, workspace_id: str, permission: Permission, target: str) -> None:
    if not principal.can(Permission.RUN_READ, workspace_id):
        record(request, principal, "access.denied", workspace_id, target, outcome="denied", detail={"permission": "run.read"})
        raise HTTPException(404, "Not found")
    if not principal.can(permission, workspace_id):
        record(request, principal, "access.denied", workspace_id, target, outcome="denied", detail={"permission": permission.value})
        raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted in this workspace"})


def _workspace_for(principal: Principal, requested: str | None, permission: Permission) -> str:
    allowed = principal.workspaces_with(permission)
    if requested:
        if requested not in allowed:
            raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted in that workspace"})
        return requested
    if len(allowed) == 1:
        return allowed[0]
    raise HTTPException(422, {"code": "workspace_required", "message": "choose a workspace_id", "workspaces": allowed}
                        ) if allowed else HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted"})


def _parse(definition: dict[str, Any]) -> Any:
    try:
        wf = parse(definition)
    except DefinitionError as exc:
        raise HTTPException(422, {"code": "invalid_workflow", "problems": _problems(exc.problems)}) from None
    problems = get_engine().check(wf)
    if problems:
        raise HTTPException(422, {"code": "invalid_workflow", "problems": _problems(problems)})
    return wf


def _workflow_row(workflow_id: str) -> dict[str, Any]:
    with get_db() as conn:
        try:
            wf, meta = store.load(conn, workflow_id)
        except store.WorkflowNotFound:
            raise HTTPException(404, "Not found") from None
    return {"definition": to_dict(wf), **meta}


def _run_row(run_id: str) -> dict[str, Any]:
    with get_db() as conn:
        try:
            return store.get_run(conn, run_id)
        except store.WorkflowNotFound:
            raise HTTPException(404, "Not found") from None


# ---- definitions
@router.get("")
async def list_workflows(principal: CurrentPrincipal) -> list[dict[str, Any]]:
    readable = principal.workspaces_with(Permission.RUN_READ)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT w.id, w.workspace_id, w.name, w.version, w.trigger_type, w.created_at, w.definition_json FROM workflows w "
            f"WHERE w.workspace_id IN ({','.join('?' * len(readable)) or 'NULL'}) AND w.version = "
            "(SELECT MAX(version) FROM workflows x WHERE x.workspace_id = w.workspace_id AND x.name = w.name) ORDER BY w.name",
            readable).fetchall()
    import json

    return [{**{k: r[k] for k in ("id", "workspace_id", "name", "version", "trigger_type", "created_at")},
             "description": json.loads(r["definition_json"]).get("description", "")} for r in rows]


@router.get("/templates")
async def templates(principal: CurrentPrincipal) -> list[dict[str, Any]]:
    return [{"name": name, "description": t["description"], "trigger": t["trigger"]["type"], "variables": t["variables"],
             "requires": requires(name)}
            for name, t in sorted(TEMPLATES.items())]


@router.post("/validate")
async def validate_definition(body: DefinitionBody, principal: CurrentPrincipal) -> dict[str, Any]:
    try:
        wf = parse(body.definition)
    except DefinitionError as exc:
        return {"ok": False, "problems": _problems(exc.problems)}
    problems = get_engine().check(wf)
    return {"ok": not problems, "problems": _problems(problems)}


@router.post("")
async def save_workflow(body: DefinitionBody, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    workspace_id = _workspace_for(principal, body.workspace_id, Permission.WORKFLOW_MANAGE)
    wf = _parse(body.definition)
    with get_db() as conn:
        workflow_id, version = store.save_version(conn, workspace_id, wf, principal.actor)
    record(request, principal, "workflow.save", workspace_id, workflow_id, detail={"name": wf.name, "version": version})
    return {"id": workflow_id, "name": wf.name, "version": version, "workspace_id": workspace_id}


# ---- runs (declared before /{workflow_id} so "runs" is not read as an id)
@router.get("/runs")
async def list_runs(principal: CurrentPrincipal) -> list[dict[str, Any]]:
    readable = principal.workspaces_with(Permission.RUN_READ)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, workflow_id, workspace_id, status, created_by, created_at, completed_at, error FROM workflow_runs "
            f"WHERE workspace_id IN ({','.join('?' * len(readable)) or 'NULL'}) ORDER BY created_at DESC LIMIT 100", readable).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    run = _run_row(run_id)
    _authorize(request, principal, run["workspace_id"], Permission.RUN_READ, run_id)
    with get_db() as conn:
        return {**run, "steps": store.steps(conn, run_id), "events": store.events(conn, run_id)}


@router.post("/runs/{run_id}/steps/{node_id}/decision")
async def decide(run_id: str, node_id: str, body: DecisionBody, request: Request, principal: CurrentPrincipal) -> dict[str, str]:
    run = _run_row(run_id)
    _authorize(request, principal, run["workspace_id"], Permission.APPROVAL_DECIDE, run_id)
    try:
        await get_engine().decide(run_id, node_id, body.decision, principal.actor)
    except WorkflowError as exc:
        record(request, principal, "workflow.decide", run["workspace_id"], run_id, outcome="refused", detail={"node": node_id})
        raise HTTPException(409, {"code": "not_pending", "message": str(exc)}) from None
    record(request, principal, "workflow.decide", run["workspace_id"], run_id, detail={"node": node_id, "decision": body.decision})
    return {"status": "ok"}


@router.post("/runs/{run_id}/steps/{node_id}/resolve")
async def resolve(run_id: str, node_id: str, body: ResolveBody, request: Request, principal: CurrentPrincipal) -> dict[str, str]:
    run = _run_row(run_id)
    _authorize(request, principal, run["workspace_id"], Permission.RUN_CONTROL, run_id)
    try:
        await get_engine().resolve(run_id, node_id, body.outcome, principal.actor)
    except WorkflowError as exc:
        raise HTTPException(409, {"code": "not_resolvable", "message": str(exc)}) from None
    record(request, principal, "workflow.resolve", run["workspace_id"], run_id, detail={"node": node_id, "outcome": body.outcome})
    return {"status": "ok"}


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, request: Request, principal: CurrentPrincipal) -> dict[str, str]:
    run = _run_row(run_id)
    _authorize(request, principal, run["workspace_id"], Permission.RUN_CONTROL, run_id)
    await get_engine().cancel(run_id, principal.actor)
    record(request, principal, "workflow.cancel", run["workspace_id"], run_id)
    return {"status": "cancelled"}


# ---- one workflow
@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    row = _workflow_row(workflow_id)
    _authorize(request, principal, row["workspace_id"], Permission.RUN_READ, workflow_id)
    return row


@router.post("/{workflow_id}/runs")
async def start_run(workflow_id: str, body: StartBody, request: Request, principal: CurrentPrincipal) -> dict[str, str]:
    row = _workflow_row(workflow_id)
    _authorize(request, principal, row["workspace_id"], Permission.RUN_CREATE, workflow_id)
    engine = get_engine()
    try:
        run_id = engine.start(workflow_id, {"type": "manual", "payload": body.payload}, variables=body.variables,
                              created_by=principal.actor)
    except WorkflowError as exc:
        raise HTTPException(422, {"code": "cannot_start", "message": str(exc)}) from None
    record(request, principal, "workflow.start", row["workspace_id"], run_id, detail={"workflow": workflow_id})
    asyncio.get_running_loop().create_task(engine.advance(str(run_id)))
    return {"run_id": str(run_id)}

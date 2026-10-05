"""Policy API. A policy attaches to a tenant-owned thing: the organisation, a workspace, or one of its workflows.

Repository- and user-scoped policies are set from the CLI only: a repository path is not owned by any tenant,
so an API caller could otherwise restrict another tenant's runs on the same path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from patchquest.api.auth import CurrentPrincipal, record
from patchquest.database import get_db
from patchquest.domain.effects import SideEffect
from patchquest.domain.identity import Permission, Principal
from patchquest.domain.policy import PolicyError, Scope
from patchquest.persistence import policies
from patchquest.runtime import policy as rt

router = APIRouter(prefix="/api/policies", tags=["policies"])


class PolicyBody(BaseModel):
    workspace_id: str
    document: dict[str, Any]
    ref: str | None = None  # for workflow scope: the workflow id; the others are implied


class ExplainBody(BaseModel):
    workspace_id: str
    action: str
    effect: str | None = None
    workflow_id: str | None = None


def _check(request: Request, principal: Principal, workspace_id: str, permission: Permission, target: str) -> None:
    if not principal.can(Permission.RUN_READ, workspace_id):
        record(request, principal, "access.denied", workspace_id, target, outcome="denied", detail={"permission": "run.read"})
        raise HTTPException(404, "Not found")
    if not principal.can(permission, workspace_id):
        record(request, principal, "access.denied", workspace_id, target, outcome="denied", detail={"permission": permission.value})
        raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted in this workspace"})


def _org_of(workspace_id: str) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Not found")
    return str(row["org_id"])


@router.get("")
async def list_policies(request: Request, principal: CurrentPrincipal, workspace_id: str) -> list[dict[str, Any]]:
    _check(request, principal, workspace_id, Permission.SETTINGS_READ, f"policies:{workspace_id}")
    chain = rt.chain_for(workspace_id=workspace_id)
    return rt.effective_chain_description(chain)


@router.put("")
async def put_policy(request: Request, principal: CurrentPrincipal, body: PolicyBody) -> dict[str, Any]:
    _check(request, principal, body.workspace_id, Permission.SETTINGS_WRITE, f"policies:{body.workspace_id}")
    try:
        scope = Scope.parse(str(body.document.get("scope", "")))
        if scope is Scope.ORGANIZATION:
            if not principal.can(Permission.ORG_MANAGE, body.workspace_id):
                raise HTTPException(403, {"code": "forbidden", "message": "only an owner can set organisation-wide policy"})
            ref = _org_of(body.workspace_id)
        elif scope is Scope.WORKSPACE:
            ref = body.workspace_id
        elif scope is Scope.WORKFLOW:
            with get_db() as conn:
                row = conn.execute("SELECT 1 FROM workflows WHERE id = ? AND workspace_id = ?", (body.ref, body.workspace_id)).fetchone()
            if not body.ref or row is None:
                raise HTTPException(404, "Not found")
            ref = body.ref
        else:
            raise HTTPException(422, {"code": "scope_not_allowed", "message": "the API sets organization, workspace and workflow policy"})
        stored = rt.store(body.document, scope_ref=ref, actor=principal.actor, org_id=principal.org_id, workspace_id=body.workspace_id)
    except PolicyError as exc:
        raise HTTPException(422, {"code": "invalid_policy", "message": str(exc)}) from None
    return {"name": stored.name, "scope": stored.scope.name.lower(), "version": stored.version, "digest": stored.digest()}


@router.delete("/{scope}/{name}")
async def disable_policy(request: Request, principal: CurrentPrincipal, scope: str, name: str, workspace_id: str,
                         ref: str | None = None) -> dict[str, bool]:
    _check(request, principal, workspace_id, Permission.SETTINGS_WRITE, f"policies:{workspace_id}")
    try:
        parsed = Scope.parse(scope)
    except PolicyError:
        raise HTTPException(404, "Not found") from None
    refs = {Scope.ORGANIZATION: _org_of(workspace_id), Scope.WORKSPACE: workspace_id, Scope.WORKFLOW: ref}
    if parsed not in refs or not refs[parsed]:
        raise HTTPException(404, "Not found")
    if parsed is Scope.ORGANIZATION and not principal.can(Permission.ORG_MANAGE, workspace_id):
        raise HTTPException(403, {"code": "forbidden", "message": "only an owner can change organisation-wide policy"})
    with get_db() as conn:
        done = policies.disable(conn, parsed, str(refs[parsed]), name)
    if not done:
        raise HTTPException(404, "Not found")
    record(request, principal, "policy.disable", workspace_id, f"{scope}:{name}")
    return {"disabled": True}


@router.post("/explain")
async def explain(request: Request, principal: CurrentPrincipal, body: ExplainBody) -> dict[str, Any]:
    _check(request, principal, body.workspace_id, Permission.SETTINGS_READ, f"policies:{body.workspace_id}")
    try:
        effect = SideEffect(body.effect.upper()) if body.effect else None
    except ValueError:
        raise HTTPException(422, {"code": "invalid_effect", "message": "unknown side effect"}) from None
    chain = rt.chain_for(workspace_id=body.workspace_id, workflow_id=body.workflow_id, user=principal.id)
    return {**rt.decide(chain, body.action, effect).to_dict(), "policies": rt.effective_chain_description(chain)}

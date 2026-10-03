"""Memory, preferences and repository profiles over HTTP. Everything is workspace-scoped and filtered by tenant in SQL.

Who may write what: organisation scope needs an owner; workspace scope needs ``settings.write``; repository and
workflow scope need ``run.create``; user scope is only ever your own. Service accounts are not people, so what
they store is recorded as ``import`` (low authority, never a preference or procedure). The API never reads a
repository: detection happens inside runs and the CLI, which validate the path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from patchquest.api.auth import CurrentPrincipal, record
from patchquest.database import get_db
from patchquest.domain import preferences as prefs
from patchquest.domain.identity import Permission, Principal
from patchquest.domain.memory import ACTIVE_KINDS, MemoryKind, MemoryRefused, Source, Status, to_public
from patchquest.domain.policy import PolicyError, Scope
from patchquest.persistence import memories
from patchquest.persistence.memories import Owner
from patchquest.runtime import memory_service as svc
from patchquest.runtime import repo_profile

router = APIRouter(prefix="/api", tags=["memory"])


class MemoryBody(BaseModel):
    workspace_id: str
    scope: str = "repository"
    ref: str | None = None
    kind: str = "repository"
    key: str
    value: Any


class PreferenceBody(BaseModel):
    workspace_id: str
    scope: str = "repository"
    ref: str | None = None
    key: str
    value: Any


class FieldBody(BaseModel):
    workspace_id: str
    path: str
    value: Any


def _owner(request: Request, principal: Principal, workspace_id: str, permission: Permission = Permission.RUN_READ) -> Owner:
    if not principal.can(Permission.RUN_READ, workspace_id):
        record(request, principal, "access.denied", workspace_id, f"knowledge:{workspace_id}", outcome="denied", detail={"permission": "run.read"})
        raise HTTPException(404, "Not found")
    if not principal.can(permission, workspace_id):
        record(request, principal, "access.denied", workspace_id, f"knowledge:{workspace_id}", outcome="denied", detail={"permission": permission.value})
        raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted in this workspace"})
    with get_db() as conn:
        return svc.owner_for(conn, workspace_id)


def _scope(name: str) -> Scope:
    try:
        return Scope.parse(name)
    except PolicyError as exc:
        raise HTTPException(422, {"code": "invalid_scope", "message": str(exc)}) from None


def _may_write(request: Request, principal: Principal, workspace_id: str, scope: Scope, ref: str | None) -> tuple[Owner, str | None]:
    """Authorise writing at ``scope`` and return the owner and the (possibly implied) reference."""
    if scope is Scope.ORGANIZATION:
        owner = _owner(request, principal, workspace_id, Permission.SETTINGS_WRITE)
        if not principal.can(Permission.ORG_MANAGE, workspace_id):
            raise HTTPException(403, {"code": "forbidden", "message": "only an owner can write organisation-wide memory"})
        return owner, None
    if scope is Scope.WORKSPACE:
        return _owner(request, principal, workspace_id, Permission.SETTINGS_WRITE), None
    if scope is Scope.USER:
        owner = _owner(request, principal, workspace_id, Permission.RUN_READ)
        return owner, principal.actor  # only ever your own
    if scope in (Scope.REPOSITORY, Scope.WORKFLOW):
        owner = _owner(request, principal, workspace_id, Permission.RUN_CREATE)
        if scope is Scope.WORKFLOW:
            with get_db() as conn:
                ok = conn.execute("SELECT 1 FROM workflows WHERE id = ? AND workspace_id = ?", (ref, workspace_id)).fetchone()
            if not ref or ok is None:
                raise HTTPException(404, "Not found")
        return owner, ref
    raise HTTPException(422, {"code": "invalid_scope", "message": "that scope cannot hold memory"})


def _source(principal: Principal) -> Source:
    return Source.USER_EXPLICIT if principal.kind == "user" else Source.IMPORT


def _refusal(exc: Exception) -> HTTPException:
    return HTTPException(422, {"code": "refused", "message": str(exc)})


def _visible_to(principal: Principal, m: Any) -> bool:
    return m.scope is not Scope.USER or m.scope_id == principal.actor


@router.get("/memories")
async def list_memories(request: Request, principal: CurrentPrincipal, workspace_id: str, repo: str | None = None, kind: str | None = None,
                        include_inactive: bool = False) -> list[dict[str, Any]]:
    owner = _owner(request, principal, workspace_id)
    try:
        kinds = (MemoryKind(kind),) if kind else None
    except ValueError:
        raise HTTPException(422, {"code": "invalid_kind", "message": f"kind is one of {', '.join(k.value for k in ACTIVE_KINDS)}"}) from None
    with get_db() as conn:
        found = memories.visible(conn, owner, repo=repo and svc.scope_id_for(owner, Scope.REPOSITORY, repo), user=principal.actor, kinds=kinds,
                                 statuses=tuple(Status) if include_inactive else (Status.ACTIVE,))
    return [to_public(m) for m in found if _visible_to(principal, m)]


@router.post("/memories", status_code=201)
async def add_memory(request: Request, principal: CurrentPrincipal, body: MemoryBody) -> dict[str, Any]:
    scope = _scope(body.scope)
    owner, ref = _may_write(request, principal, body.workspace_id, scope, body.ref)
    try:
        with get_db() as conn:
            _, memory = svc.remember(conn, owner, scope=scope, ref=ref, key=body.key, value=body.value, kind=MemoryKind(body.kind),
                                     source=_source(principal), reason="stated through the API", actor=principal.actor)
    except (MemoryRefused, ValueError) as exc:
        raise _refusal(exc) from None
    return to_public(memory)


@router.delete("/memories/{memory_id}")
async def forget_memory(request: Request, principal: CurrentPrincipal, memory_id: str, workspace_id: str) -> dict[str, bool]:
    owner = _owner(request, principal, workspace_id)
    with get_db() as conn:
        found = memories.find(conn, owner, memory_id)
    if found is None or not _visible_to(principal, found):
        raise HTTPException(404, "Not found")
    _may_write(request, principal, workspace_id, found.scope, found.scope_id if found.scope is Scope.WORKFLOW else None)
    with get_db() as conn:
        svc.forget(conn, owner, found.id, principal.actor)
    return {"forgotten": True}


@router.get("/preferences")
async def list_preferences(request: Request, principal: CurrentPrincipal, workspace_id: str, repo: str | None = None) -> dict[str, Any]:
    owner = _owner(request, principal, workspace_id)
    with get_db() as conn:
        resolved = svc.resolve_preferences(conn, owner, repo=repo and svc.scope_id_for(owner, Scope.REPOSITORY, repo), user=principal.actor)
    catalogue = {k: {"description": s.description, "applied_by": s.applied_by} for k, s in prefs.PREFERENCES.items()}
    return {k: {**r.to_dict(), **catalogue[k]} for k, r in resolved.items()}


@router.put("/preferences")
async def set_preference(request: Request, principal: CurrentPrincipal, body: PreferenceBody) -> dict[str, Any]:
    if principal.kind != "user":
        raise HTTPException(403, {"code": "forbidden", "message": "preferences are set by people, not service accounts"})
    scope = _scope(body.scope)
    owner, ref = _may_write(request, principal, body.workspace_id, scope, body.ref)
    try:
        with get_db() as conn:
            return to_public(svc.set_preference(conn, owner, scope=scope, ref=ref, key=body.key, value=body.value, actor=principal.actor))
    except MemoryRefused as exc:
        raise _refusal(exc) from None


@router.delete("/preferences")
async def clear_preference(request: Request, principal: CurrentPrincipal, workspace_id: str, key: str, scope: str = "repository",
                           ref: str | None = None) -> dict[str, bool]:
    parsed = _scope(scope)
    owner, implied = _may_write(request, principal, workspace_id, parsed, ref)
    with get_db() as conn:
        done = svc.clear_preference(conn, owner, scope=parsed, ref=implied, key=key, actor=principal.actor)
    if not done:
        raise HTTPException(404, "Not found")
    return {"cleared": True}


@router.get("/repositories/profile")
async def get_profile(request: Request, principal: CurrentPrincipal, workspace_id: str, path: str = Query(...)) -> dict[str, Any]:
    owner = _owner(request, principal, workspace_id)
    with get_db() as conn:
        return {"path": svc.scope_id_for(owner, Scope.REPOSITORY, path), "profile": repo_profile.profile(conn, owner, path)}


@router.put("/repositories/profile/{field}")
async def set_profile_field(request: Request, principal: CurrentPrincipal, field: str, body: FieldBody) -> dict[str, Any]:
    if principal.kind != "user":
        raise HTTPException(403, {"code": "forbidden", "message": "profile corrections are made by people"})
    owner = _owner(request, principal, body.workspace_id, Permission.RUN_CREATE)
    try:
        with get_db() as conn:
            return to_public(repo_profile.override(conn, owner, body.path, field, body.value, principal.actor))
    except (MemoryRefused, ValueError) as exc:
        raise _refusal(exc) from None

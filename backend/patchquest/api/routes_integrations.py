"""Integrations API: connect external systems to a workspace. Secrets are write-only: they can be set and replaced, never read."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from patchquest import secrets_store
from patchquest.api.auth import CurrentPrincipal, record
from patchquest.database import get_db
from patchquest.domain.identity import Permission, Principal
from patchquest.integrations import service
from patchquest.integrations.kinds import KINDS

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class CreateBody(BaseModel):
    workspace_id: str
    kind: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Any] = Field(default_factory=dict)


class UpdateBody(BaseModel):
    workspace_id: str
    name: str | None = None
    config: dict[str, Any] | None = None
    secrets: dict[str, Any] | None = None
    enabled: bool | None = None


def _check(request: Request, principal: Principal, workspace_id: str, permission: Permission, target: str) -> None:
    if not principal.can(Permission.RUN_READ, workspace_id):
        record(request, principal, "access.denied", workspace_id, target, outcome="denied", detail={"permission": "run.read"})
        raise HTTPException(404, "Not found")
    if not principal.can(permission, workspace_id):
        record(request, principal, "access.denied", workspace_id, target, outcome="denied", detail={"permission": permission.value})
        raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted in this workspace"})


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(422, {"code": "refused", "message": str(exc)})


@router.get("/kinds")
async def kinds(principal: CurrentPrincipal) -> dict[str, Any]:
    """What can be connected, and what each needs. Also says whether encrypted secret storage is available."""
    return {"kinds": [k.describe() for k in KINDS.values()], "stored_secrets_available": secrets_store.available()}


@router.get("")
async def list_integrations(request: Request, principal: CurrentPrincipal, workspace_id: str) -> list[dict[str, Any]]:
    _check(request, principal, workspace_id, Permission.SETTINGS_READ, f"integrations:{workspace_id}")
    with get_db() as conn:
        return service.list_for(conn, workspace_id)


@router.post("", status_code=201)
async def create_integration(request: Request, principal: CurrentPrincipal, body: CreateBody) -> dict[str, Any]:
    _check(request, principal, body.workspace_id, Permission.CONNECTOR_MANAGE, f"integrations:{body.workspace_id}")
    try:
        with get_db() as conn:
            integration_id = service.create(conn, body.workspace_id, body.kind, body.name, body.config, body.secrets, principal.actor)
            return service.public_view(service.get(conn, body.workspace_id, integration_id))
    except service.IntegrationError as exc:
        raise _refused(exc) from None


@router.put("/{integration_id}")
async def update_integration(request: Request, principal: CurrentPrincipal, integration_id: str, body: UpdateBody) -> dict[str, Any]:
    _check(request, principal, body.workspace_id, Permission.CONNECTOR_MANAGE, integration_id)
    try:
        with get_db() as conn:
            service.update(conn, body.workspace_id, integration_id, principal.actor, name=body.name, config=body.config,
                           secrets=body.secrets, enabled=body.enabled)
            return service.public_view(service.get(conn, body.workspace_id, integration_id))
    except LookupError:
        raise HTTPException(404, "Not found") from None
    except service.IntegrationError as exc:
        raise _refused(exc) from None


@router.delete("/{integration_id}")
async def delete_integration(request: Request, principal: CurrentPrincipal, integration_id: str, workspace_id: str) -> dict[str, bool]:
    _check(request, principal, workspace_id, Permission.CONNECTOR_MANAGE, integration_id)
    try:
        with get_db() as conn:
            service.delete(conn, workspace_id, integration_id, principal.actor)
    except LookupError:
        raise HTTPException(404, "Not found") from None
    return {"deleted": True}


@router.post("/{integration_id}/test")
async def test_integration(request: Request, principal: CurrentPrincipal, integration_id: str, workspace_id: str) -> dict[str, Any]:
    _check(request, principal, workspace_id, Permission.CONNECTOR_MANAGE, integration_id)
    try:
        with get_db() as conn:
            result = service.test_connection(conn, workspace_id, integration_id, principal.actor)
    except LookupError:
        raise HTTPException(404, "Not found") from None
    record(request, principal, "integration.test", workspace_id, integration_id, outcome="ok" if result["ok"] else "failed")
    return result


@router.get("/{integration_id}/events")
async def integration_events(request: Request, principal: CurrentPrincipal, integration_id: str, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Recent inbound deliveries from this integration: what arrived, and what became of it."""
    _check(request, principal, workspace_id, Permission.SETTINGS_READ, integration_id)
    with get_db() as conn:
        try:
            row = service.get(conn, workspace_id, integration_id)
        except LookupError:
            raise HTTPException(404, "Not found") from None
        rows = conn.execute("SELECT source, external_id, received_at, status, run_id FROM connector_events WHERE workspace_id = ? AND source = ? "
                            "ORDER BY received_at DESC LIMIT ?", (workspace_id, row["kind"], max(1, min(limit, 200)))).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]

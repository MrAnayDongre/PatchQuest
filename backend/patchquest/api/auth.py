"""API authentication and authorisation.

``authenticate_request`` turns the bearer token into a ``Principal`` (global dependency, so every route
except health is covered). Routes then ask for a permission:

* ``require(perm)``             - the principal holds ``perm`` in at least one workspace
* ``run_access(perm)``          - object-level: the run's workspace grants ``perm``. A run the caller may not
                                  even read is reported as *not found*, so ids cannot be probed across tenants.
* ``local_scope(read, write)``  - for features that are not yet tenant-scoped (scheduler, calendar, memory, ...):
                                  only usable while a single workspace exists, then by permission.

Modes: with no token configured (env or database) the API is local and loopback-only, and every caller is the
implicit local owner. With a token, the caller is whoever it belongs to.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from patchquest.database import get_db
from patchquest.domain.identity import LOCAL_ORG_ID, LOCAL_WORKSPACE_ID, Permission, Principal, Role
from patchquest.persistence import identity as ids
from patchquest.security import EXEMPT_PATHS, configured_token

_STREAM_SUFFIX = "/stream"
LEGACY_TOKEN_PRINCIPAL = Principal("api-token", "local", "shared API token", LOCAL_ORG_ID, {LOCAL_WORKSPACE_ID: Role.OWNER})


def _supplied_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    if request.url.path.endswith(_STREAM_SUFFIX):  # EventSource cannot set headers
        return request.query_params.get("token", "")
    return ""


def _remote(request: Request) -> str | None:
    return request.client.host if request.client else None


def _unauthorized(request: Request, why: str) -> HTTPException:
    with get_db() as conn:
        ids.audit(conn, "auth.failed", actor="anonymous", outcome="denied", detail={"path": request.url.path, "why": why},
                  remote=_remote(request))
    return HTTPException(401, "Missing or invalid API token", headers={"WWW-Authenticate": "Bearer"})


async def authenticate_request(request: Request) -> Principal | None:
    # Only /api/* is protected: everything else is the static UI (public files) and the health probes.
    if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
        return None
    supplied, legacy = _supplied_token(request), configured_token()
    with get_db() as conn:
        auth_required = legacy is not None or ids.has_tokens(conn)
        if not auth_required:
            principal: Principal | None = ids.local_principal()
        elif not supplied:
            raise _unauthorized(request, "no token")
        elif legacy is not None and secrets.compare_digest(supplied.encode(), legacy.encode()):
            principal = LEGACY_TOKEN_PRINCIPAL
        else:
            try:
                principal = ids.authenticate(conn, supplied)
            except ids.UnknownToken:
                principal = None
    if principal is None:
        raise _unauthorized(request, "unknown token")
    request.state.principal = principal
    return principal


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:  # a route without the global auth dependency: refuse rather than assume
        raise HTTPException(401, "Not authenticated")
    return principal


def _denied(request: Request, principal: Principal, permission: Permission, workspace_id: str | None, target: str | None = None) -> None:
    with get_db() as conn:
        ids.audit(conn, "access.denied", actor=principal.actor, outcome="denied", workspace_id=workspace_id,
                  org_id=principal.org_id, target=target, detail={"permission": permission.value, "path": request.url.path},
                  remote=_remote(request))


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require(permission: Permission) -> Callable[..., Principal]:
    def dependency(request: Request, principal: CurrentPrincipal) -> Principal:
        if not principal.workspaces_with(permission):
            _denied(request, principal, permission, None)
            raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted"})
        return principal

    return dependency


@dataclass(frozen=True)
class RunAccess:
    principal: Principal
    run: dict[str, Any]

    @property
    def workspace_id(self) -> str:
        return self.run["workspace_id"]


def run_access(permission: Permission) -> Callable[..., RunAccess]:
    def dependency(run_id: str, request: Request, principal: CurrentPrincipal) -> RunAccess:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        # Same answer for "does not exist" and "not yours": ids must not reveal other tenants' runs.
        if row is None or not principal.can(Permission.RUN_READ, row["workspace_id"]):
            if row is not None:
                _denied(request, principal, Permission.RUN_READ, row["workspace_id"], run_id)
            raise HTTPException(404, "Run not found")
        if not principal.can(permission, row["workspace_id"]):
            _denied(request, principal, permission, row["workspace_id"], run_id)
            raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted in this workspace"})
        return RunAccess(principal, {k: row[k] for k in row.keys()})

    return dependency


def local_scope(read: Permission, write: Permission) -> Callable[..., Principal]:
    """Gate for features whose data is not yet tenant-scoped: usable only while there is a single workspace."""

    def dependency(request: Request, principal: CurrentPrincipal) -> Principal:
        with get_db() as conn:
            multi = ids.workspace_count(conn) > 1
        if multi:
            raise HTTPException(403, {"code": "not_tenant_scoped",
                                      "message": "This feature is not available with more than one workspace yet"})
        permission = read if request.method in ("GET", "HEAD") else write
        if not principal.can(permission, LOCAL_WORKSPACE_ID):
            _denied(request, principal, permission, LOCAL_WORKSPACE_ID)
            raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted"})
        return principal

    return dependency


def record(request: Request, principal: Principal, action: str, workspace_id: str | None, target: str | None = None,
           outcome: str = "ok", detail: dict[str, Any] | None = None) -> None:
    with get_db() as conn:
        ids.audit(conn, action, actor=principal.actor, outcome=outcome, workspace_id=workspace_id, org_id=principal.org_id,
                  target=target, detail=detail, remote=_remote(request))

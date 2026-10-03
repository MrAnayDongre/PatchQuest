"""Repositories, projects and teams.

A repository belongs to one workspace; registering a path claims it for that workspace only. Projects group a
workspace's repositories and can be owned by a team. Teams belong to an organisation and are managed by its
owners; granting a team a role in a workspace is a workspace admin's call, bounded by what that admin may grant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from patchquest.api.auth import CurrentPrincipal, record
from patchquest.database import get_db
from patchquest.domain.identity import Permission, Principal, Role, may_grant
from patchquest.domain.tenancy import RepositoryClaimed, TenancyError
from patchquest.persistence import tenancy
from patchquest.security import RepoPathError, validate_repo_path

router = APIRouter(prefix="/api", tags=["tenancy"])


class RepositoryBody(BaseModel):
    workspace_id: str
    path: str
    name: str | None = None
    project_id: str | None = None


class AssignBody(BaseModel):
    workspace_id: str
    project_id: str | None = None


class ProjectBody(BaseModel):
    workspace_id: str
    name: str
    team_id: str | None = None


class ProjectTeamBody(BaseModel):
    workspace_id: str
    team_id: str | None = None


class TeamBody(BaseModel):
    workspace_id: str
    name: str


class MemberBody(BaseModel):
    workspace_id: str
    principal_id: str


class TeamRoleBody(BaseModel):
    workspace_id: str
    role: str


def _check(request: Request, principal: Principal, workspace_id: str, permission: Permission) -> str:
    """Authorise and return the workspace's organisation. A workspace you cannot read does not exist."""
    if not principal.can(Permission.RUN_READ, workspace_id):
        record(request, principal, "access.denied", workspace_id, f"tenancy:{workspace_id}", outcome="denied", detail={"permission": "run.read"})
        raise HTTPException(404, "Not found")
    if not principal.can(permission, workspace_id):
        record(request, principal, "access.denied", workspace_id, f"tenancy:{workspace_id}", outcome="denied", detail={"permission": permission.value})
        raise HTTPException(403, {"code": "forbidden", "message": f"{permission.value} is not permitted in this workspace"})
    with get_db() as conn:
        row = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Not found")
    return str(row["org_id"])


def _refused(exc: Exception, status: int = 422) -> HTTPException:
    return HTTPException(status, {"code": "refused", "message": str(exc)})


# ---------------------------------------------------------------- repositories
@router.get("/repositories")
async def list_repositories(request: Request, principal: CurrentPrincipal, workspace_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
    _check(request, principal, workspace_id, Permission.RUN_READ)
    with get_db() as conn:
        return tenancy.list_repositories(conn, workspace_id, project_id)


@router.post("/repositories", status_code=201)
async def register_repository(request: Request, principal: CurrentPrincipal, body: RepositoryBody) -> dict[str, Any]:
    _check(request, principal, body.workspace_id, Permission.REPOSITORY_MANAGE)
    try:
        path = validate_repo_path(body.path)
        with get_db() as conn:
            repo = tenancy.register_repository(conn, body.workspace_id, path, body.name, body.project_id, principal.actor)
    except RepoPathError as exc:
        raise _refused(exc) from None
    except RepositoryClaimed as exc:
        record(request, principal, "repository.claim_refused", body.workspace_id, path, outcome="denied")
        raise _refused(exc, 409) from None
    except TenancyError as exc:
        raise _refused(exc) from None
    record(request, principal, "repository.register", body.workspace_id, repo["id"], detail={"path": repo["path"]})
    return repo


@router.delete("/repositories/{repo_id}")
async def unregister_repository(request: Request, principal: CurrentPrincipal, repo_id: str, workspace_id: str) -> dict[str, bool]:
    _check(request, principal, workspace_id, Permission.REPOSITORY_MANAGE)
    with get_db() as conn:
        if not tenancy.archive_repository(conn, workspace_id, repo_id):
            raise HTTPException(404, "Not found")
    record(request, principal, "repository.unregister", workspace_id, repo_id)
    return {"unregistered": True}


@router.put("/repositories/{repo_id}/project")
async def assign_repository(request: Request, principal: CurrentPrincipal, repo_id: str, body: AssignBody) -> dict[str, bool]:
    _check(request, principal, body.workspace_id, Permission.REPOSITORY_MANAGE)
    try:
        with get_db() as conn:
            tenancy.assign_repository(conn, body.workspace_id, repo_id, body.project_id)
    except TenancyError as exc:
        raise HTTPException(404, "Not found") from exc
    record(request, principal, "repository.assign", body.workspace_id, repo_id, detail={"project_id": body.project_id})
    return {"assigned": True}


# ---------------------------------------------------------------- projects
@router.get("/projects")
async def list_projects(request: Request, principal: CurrentPrincipal, workspace_id: str) -> list[dict[str, Any]]:
    _check(request, principal, workspace_id, Permission.RUN_READ)
    with get_db() as conn:
        return tenancy.list_projects(conn, workspace_id)


@router.post("/projects", status_code=201)
async def create_project(request: Request, principal: CurrentPrincipal, body: ProjectBody) -> dict[str, str]:
    org = _check(request, principal, body.workspace_id, Permission.REPOSITORY_MANAGE)
    try:
        with get_db() as conn:
            project_id = tenancy.create_project(conn, org, body.workspace_id, body.name, body.team_id)
    except TenancyError as exc:
        raise _refused(exc) from None
    record(request, principal, "project.create", body.workspace_id, project_id, detail={"team_id": body.team_id})
    return {"id": project_id}


@router.put("/projects/{project_id}/team")
async def set_project_team(request: Request, principal: CurrentPrincipal, project_id: str, body: ProjectTeamBody) -> dict[str, bool]:
    org = _check(request, principal, body.workspace_id, Permission.REPOSITORY_MANAGE)
    try:
        with get_db() as conn:
            tenancy.set_project_team(conn, org, body.workspace_id, project_id, body.team_id)
    except TenancyError as exc:
        raise HTTPException(404, "Not found") from exc
    record(request, principal, "project.set_team", body.workspace_id, project_id, detail={"team_id": body.team_id})
    return {"updated": True}


# ---------------------------------------------------------------- teams
@router.get("/teams")
async def list_teams(request: Request, principal: CurrentPrincipal, workspace_id: str) -> list[dict[str, Any]]:
    org = _check(request, principal, workspace_id, Permission.SETTINGS_READ)
    with get_db() as conn:
        return tenancy.list_teams(conn, org)


@router.post("/teams", status_code=201)
async def create_team(request: Request, principal: CurrentPrincipal, body: TeamBody) -> dict[str, str]:
    org = _check(request, principal, body.workspace_id, Permission.ORG_MANAGE)
    try:
        with get_db() as conn:
            team_id = tenancy.create_team(conn, org, body.name)
    except TenancyError as exc:
        raise _refused(exc) from None
    record(request, principal, "team.create", body.workspace_id, team_id)
    return {"id": team_id}


@router.post("/teams/{team_id}/members")
async def add_member(request: Request, principal: CurrentPrincipal, team_id: str, body: MemberBody) -> dict[str, bool]:
    org = _check(request, principal, body.workspace_id, Permission.ORG_MANAGE)
    try:
        with get_db() as conn:
            tenancy.add_member(conn, org, team_id, body.principal_id)
    except TenancyError as exc:
        raise _refused(exc) from None
    record(request, principal, "team.add_member", body.workspace_id, team_id, detail={"principal_id": body.principal_id})
    return {"added": True}


@router.delete("/teams/{team_id}/members/{principal_id}")
async def remove_member(request: Request, principal: CurrentPrincipal, team_id: str, principal_id: str, workspace_id: str) -> dict[str, bool]:
    org = _check(request, principal, workspace_id, Permission.ORG_MANAGE)
    try:
        with get_db() as conn:
            removed = tenancy.remove_member(conn, org, team_id, principal_id)
    except TenancyError as exc:
        raise HTTPException(404, "Not found") from exc
    record(request, principal, "team.remove_member", workspace_id, team_id, detail={"principal_id": principal_id})
    return {"removed": removed}


@router.put("/teams/{team_id}/roles")
async def grant_role(request: Request, principal: CurrentPrincipal, team_id: str, body: TeamRoleBody) -> dict[str, str]:
    org = _check(request, principal, body.workspace_id, Permission.MEMBERS_MANAGE)
    try:
        role = Role(body.role.upper())
    except ValueError:
        raise _refused(ValueError(f"role is one of {', '.join(r.value for r in Role)}")) from None
    granter = principal.role_in(body.workspace_id)
    if granter is None or not may_grant(granter, role):
        raise HTTPException(403, {"code": "forbidden", "message": f"you cannot grant {role.value}"})
    try:
        with get_db() as conn:
            tenancy.grant_team_role(conn, org, team_id, body.workspace_id, role)
    except TenancyError as exc:
        raise HTTPException(404, "Not found") from exc
    record(request, principal, "team.grant_role", body.workspace_id, team_id, detail={"role": role.value})
    return {"role": role.value}


@router.delete("/teams/{team_id}/roles/{workspace_id}")
async def revoke_role(request: Request, principal: CurrentPrincipal, team_id: str, workspace_id: str) -> dict[str, bool]:
    org = _check(request, principal, workspace_id, Permission.MEMBERS_MANAGE)
    with get_db() as conn:
        if tenancy.get_team(conn, org, team_id) is None:
            raise HTTPException(404, "Not found")
        removed = tenancy.revoke_team_role(conn, team_id, workspace_id)
    record(request, principal, "team.revoke_role", workspace_id, team_id)
    return {"revoked": removed}

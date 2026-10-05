"""Teams, projects and repositories, always filtered by the owning organisation or workspace in SQL."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from patchquest.domain.identity import Role
from patchquest.domain.tenancy import (
    IMPLICIT_WORKSPACE,
    ProjectAccessDenied,
    RepositoryClaimed,
    RepositoryNotRegistered,
    TenancyError,
    clean_name,
    strongest,
)
from patchquest.persistence.ledger import now_iso


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if r is None else {k: r[k] for k in r.keys()}


# ------------------------------------------------------------------ teams
def create_team(conn: sqlite3.Connection, org_id: str, name: str) -> str:
    name = clean_name(name, "team")
    if conn.execute("SELECT 1 FROM teams WHERE org_id = ? AND name = ?", (org_id, name)).fetchone():
        raise TenancyError(f"a team named '{name}' already exists")
    team_id = _id("team")
    conn.execute("INSERT INTO teams (id, org_id, name, created_at) VALUES (?, ?, ?, ?)", (team_id, org_id, name, now_iso()))
    return team_id


def get_team(conn: sqlite3.Connection, org_id: str, team_id: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM teams WHERE id = ? AND org_id = ?", (team_id, org_id)).fetchone())


def list_teams(conn: sqlite3.Connection, org_id: str) -> list[dict[str, Any]]:
    teams = []
    for t in conn.execute("SELECT * FROM teams WHERE org_id = ? ORDER BY name", (org_id,)):
        members = [m["principal_id"] for m in conn.execute("SELECT principal_id FROM team_members WHERE team_id = ? ORDER BY principal_id", (t["id"],))]
        roles = {r["workspace_id"]: r["role"] for r in conn.execute("SELECT workspace_id, role FROM team_roles WHERE team_id = ?", (t["id"],))}
        teams.append({**{k: t[k] for k in t.keys()}, "members": members, "roles": roles})
    return teams


def add_member(conn: sqlite3.Connection, org_id: str, team_id: str, principal_id: str) -> None:
    if get_team(conn, org_id, team_id) is None:
        raise TenancyError("no such team")
    if conn.execute("SELECT 1 FROM principals WHERE id = ? AND org_id = ?", (principal_id, org_id)).fetchone() is None:
        raise TenancyError("that person is not in this organisation")
    conn.execute("INSERT OR IGNORE INTO team_members (team_id, principal_id) VALUES (?, ?)", (team_id, principal_id))


def remove_member(conn: sqlite3.Connection, org_id: str, team_id: str, principal_id: str) -> bool:
    if get_team(conn, org_id, team_id) is None:
        raise TenancyError("no such team")
    return conn.execute("DELETE FROM team_members WHERE team_id = ? AND principal_id = ?", (team_id, principal_id)).rowcount > 0


def grant_team_role(conn: sqlite3.Connection, org_id: str, team_id: str, workspace_id: str, role: Role) -> None:
    if get_team(conn, org_id, team_id) is None:
        raise TenancyError("no such team")
    if conn.execute("SELECT 1 FROM workspaces WHERE id = ? AND org_id = ?", (workspace_id, org_id)).fetchone() is None:
        raise TenancyError("a team can only be granted roles in workspaces of its own organisation")
    conn.execute("INSERT INTO team_roles (team_id, workspace_id, role) VALUES (?, ?, ?) "
                 "ON CONFLICT(team_id, workspace_id) DO UPDATE SET role = excluded.role", (team_id, workspace_id, role.value))


def revoke_team_role(conn: sqlite3.Connection, team_id: str, workspace_id: str) -> bool:
    return conn.execute("DELETE FROM team_roles WHERE team_id = ? AND workspace_id = ?", (team_id, workspace_id)).rowcount > 0


def team_roles_for(conn: sqlite3.Connection, principal_id: str) -> dict[str, list[Role]]:
    out: dict[str, list[Role]] = {}
    for r in conn.execute("SELECT tr.workspace_id, tr.role FROM team_roles tr JOIN team_members tm ON tm.team_id = tr.team_id "
                          "WHERE tm.principal_id = ?", (principal_id,)):
        out.setdefault(r["workspace_id"], []).append(Role(r["role"]))
    return out


def is_member(conn: sqlite3.Connection, team_id: str, principal_id: str) -> bool:
    return conn.execute("SELECT 1 FROM team_members WHERE team_id = ? AND principal_id = ?", (team_id, principal_id)).fetchone() is not None


# ------------------------------------------------------------------ projects
def create_project(conn: sqlite3.Connection, org_id: str, workspace_id: str, name: str, team_id: str | None) -> str:
    name = clean_name(name, "project")
    if team_id is not None and get_team(conn, org_id, team_id) is None:
        raise TenancyError("no such team in this organisation")
    if conn.execute("SELECT 1 FROM projects WHERE workspace_id = ? AND name = ?", (workspace_id, name)).fetchone():
        raise TenancyError(f"a project named '{name}' already exists in this workspace")
    project_id = _id("proj")
    conn.execute("INSERT INTO projects (id, workspace_id, name, team_id, created_at) VALUES (?, ?, ?, ?, ?)",
                 (project_id, workspace_id, name, team_id, now_iso()))
    return project_id


def get_project(conn: sqlite3.Connection, workspace_id: str, project_id: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM projects WHERE id = ? AND workspace_id = ?", (project_id, workspace_id)).fetchone())


def list_projects(conn: sqlite3.Connection, workspace_id: str) -> list[dict[str, Any]]:
    out = []
    for p in conn.execute("SELECT * FROM projects WHERE workspace_id = ? ORDER BY name", (workspace_id,)):
        repos = [r["id"] for r in conn.execute("SELECT id FROM repositories WHERE project_id = ? AND archived_at IS NULL ORDER BY name", (p["id"],))]
        out.append({**{k: p[k] for k in p.keys()}, "repositories": repos})
    return out


def set_project_team(conn: sqlite3.Connection, org_id: str, workspace_id: str, project_id: str, team_id: str | None) -> None:
    if get_project(conn, workspace_id, project_id) is None:
        raise TenancyError("no such project")
    if team_id is not None and get_team(conn, org_id, team_id) is None:
        raise TenancyError("no such team in this organisation")
    conn.execute("UPDATE projects SET team_id = ? WHERE id = ?", (team_id, project_id))


# ------------------------------------------------------------------ repositories
def _overlaps(a: str, b: str) -> bool:
    pa, pb = Path(a), Path(b)
    return pa == pb or pa.is_relative_to(pb) or pb.is_relative_to(pa)


MAX_SCAN_ENTRIES = 3000


def check_registrable(path: str) -> None:
    """A registered path must be one repository, not a directory that holds others: claiming ``/srv/repos`` would give a
    workspace every checkout beneath it (including other tenants') and lock them all out of registering.

    A directory with a ``.git`` is a repository root. Otherwise it is refused if a ``.git`` exists within three levels
    (the scan is bounded)."""
    import os

    root = Path(path)
    if (root / ".git").exists():
        return
    seen = 0
    for dirpath, dirnames, _files in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if ".git" in dirnames:
            raise TenancyError("that directory contains other repositories; register each repository on its own")
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".venv", "venv", "__pycache__")] if depth < 3 else []
        seen += len(dirnames)
        if seen > MAX_SCAN_ENTRIES:
            raise TenancyError("that directory is too large to register as one repository; register the repository's own folder")


def register_repository(conn: sqlite3.Connection, workspace_id: str, path: str, name: str | None, project_id: str | None,
                        created_by: str) -> dict[str, Any]:
    """Claim a canonical path for a workspace. Idempotent for the owner; refused if any other workspace has a path
    that is the same, inside it, or around it."""
    canonical = str(Path(path).resolve())
    for r in conn.execute("SELECT * FROM repositories WHERE archived_at IS NULL"):
        if r["workspace_id"] == workspace_id:
            if r["path"] == canonical:
                return dict(_row(r) or {})
        elif _overlaps(canonical, r["path"]):
            raise RepositoryClaimed("that path (or one inside or around it) is already registered by another workspace")
    if project_id is not None and get_project(conn, workspace_id, project_id) is None:
        raise TenancyError("no such project in this workspace")
    repo_id = _id("repo")
    label = clean_name(name or Path(canonical).name, "repository")
    conn.execute("INSERT INTO repositories (id, workspace_id, project_id, name, path, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (repo_id, workspace_id, project_id, label, canonical, created_by, now_iso()))
    return dict(_row(conn.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,)).fetchone()) or {})


def get_repository(conn: sqlite3.Connection, workspace_id: str, repo_id: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM repositories WHERE id = ? AND workspace_id = ?", (repo_id, workspace_id)).fetchone())


def list_repositories(conn: sqlite3.Connection, workspace_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM repositories WHERE workspace_id = ? AND archived_at IS NULL", [workspace_id]
    if project_id:
        sql, params = sql + " AND project_id = ?", [*params, project_id]
    return [dict(_row(r) or {}) for r in conn.execute(sql + " ORDER BY name", params)]


def archive_repository(conn: sqlite3.Connection, workspace_id: str, repo_id: str) -> bool:
    return conn.execute("UPDATE repositories SET archived_at = ? WHERE id = ? AND workspace_id = ? AND archived_at IS NULL",
                        (now_iso(), repo_id, workspace_id)).rowcount > 0


def assign_repository(conn: sqlite3.Connection, workspace_id: str, repo_id: str, project_id: str | None) -> None:
    if get_repository(conn, workspace_id, repo_id) is None:
        raise TenancyError("no such repository")
    if project_id is not None and get_project(conn, workspace_id, project_id) is None:
        raise TenancyError("no such project in this workspace")
    conn.execute("UPDATE repositories SET project_id = ? WHERE id = ?", (project_id, repo_id))


def repository_for_path(conn: sqlite3.Connection, workspace_id: str, path: str) -> dict[str, Any] | None:
    """The workspace's registered repository that is, or encloses, ``path``."""
    canonical = Path(path).resolve()
    best: dict[str, Any] | None = None
    for r in conn.execute("SELECT * FROM repositories WHERE workspace_id = ? AND archived_at IS NULL", (workspace_id,)):
        root = Path(r["path"])
        if canonical == root or canonical.is_relative_to(root):
            if best is None or len(r["path"]) > len(best["path"]):
                best = dict(_row(r) or {})
    return best


def check_run_allowed(conn: sqlite3.Connection, *, workspace_id: str, repo_path: str, actor: str | None) -> dict[str, Any] | None:
    """Raise unless ``actor`` may start a run on ``repo_path`` in this workspace. Returns the registered repository, if any.

    A named workspace may only act on repositories it has registered. If the repository's project belongs to a team,
    the person must be in that team or hold an admin role in the workspace; work started by the system (no person)
    is not restricted here, because it is already governed by whoever may manage the workflow.
    """
    if workspace_id == IMPLICIT_WORKSPACE:
        return None
    repo = repository_for_path(conn, workspace_id, repo_path)
    if repo is None:
        raise RepositoryNotRegistered(repo_path, workspace_id)
    project = get_project(conn, workspace_id, repo["project_id"]) if repo["project_id"] else None
    if project and project["team_id"] and actor and ":" in actor:
        kind, _, principal_id = actor.partition(":")
        if kind in ("user", "service"):
            from patchquest.persistence import identity

            principal = identity.load_principal(conn, principal_id)
            role = principal.role_in(workspace_id) if principal else None
            if not (is_member(conn, project["team_id"], principal_id) or role in (Role.OWNER, Role.ADMIN)):
                raise ProjectAccessDenied(project["name"])
    return repo


def effective_role(direct: Role | None, via_teams: list[Role]) -> Role | None:
    return strongest([r for r in (direct, *via_teams) if r is not None])

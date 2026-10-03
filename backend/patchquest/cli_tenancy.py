"""``patchquest repos|projects|teams``: set up who owns what. Direct database access, like ``admin``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from patchquest.domain.identity import LOCAL_WORKSPACE_ID, Role
from patchquest.domain.tenancy import TenancyError
from patchquest.security import RepoPathError, validate_repo_path


def register(sub: Any) -> None:
    def ws(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", default=LOCAL_WORKSPACE_ID)
        p.add_argument("--json", action="store_true")

    repos = sub.add_parser("repos", help="repositories a workspace may act on").add_subparsers(dest="repos_cmd", required=True)
    a = repos.add_parser("add", help="register a path for a workspace (it can belong to only one)")
    a.add_argument("path")
    a.add_argument("--name")
    a.add_argument("--project")
    ws(a)
    ws(repos.add_parser("list"))
    r = repos.add_parser("remove")
    r.add_argument("id")
    ws(r)

    projects = sub.add_parser("projects", help="group repositories; optionally give them to a team").add_subparsers(dest="projects_cmd", required=True)
    pa = projects.add_parser("add")
    pa.add_argument("name")
    pa.add_argument("--team", help="team id: only its members and workspace admins start runs on the project's repositories")
    ws(pa)
    ws(projects.add_parser("list"))
    pt = projects.add_parser("assign", help="put a repository in a project")
    pt.add_argument("repo_id")
    pt.add_argument("project_id")
    ws(pt)

    teams = sub.add_parser("teams", help="groups of people in an organisation").add_subparsers(dest="teams_cmd", required=True)
    ta = teams.add_parser("add")
    ta.add_argument("name")
    ws(ta)
    ws(teams.add_parser("list"))
    tm = teams.add_parser("member")
    tm.add_argument("op", choices=["add", "remove"])
    tm.add_argument("team_id")
    tm.add_argument("principal_id")
    ws(tm)
    tg = teams.add_parser("grant", help="give every member of a team a role in a workspace")
    tg.add_argument("team_id")
    tg.add_argument("role", choices=[r.value for r in Role])
    ws(tg)


def _out(args: argparse.Namespace, obj: Any, human: str) -> int:
    print(json.dumps(obj, indent=2, default=str) if args.json else human)
    return 0


def run(args: argparse.Namespace) -> int:
    from patchquest.database import get_db
    from patchquest.persistence import tenancy

    try:
        with get_db() as conn:
            row = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (args.workspace,)).fetchone()
            if row is None:
                raise TenancyError(f"no such workspace: {args.workspace}")
            org = str(row["org_id"])
            if args.cmd == "repos":
                if args.repos_cmd == "add":
                    safe = validate_repo_path(args.path)
                    tenancy.check_registrable(safe)
                    repo = tenancy.register_repository(conn, args.workspace, safe, args.name, args.project, "cli")
                    return _out(args, repo, f"registered {repo['path']} as {repo['id']}")
                if args.repos_cmd == "list":
                    found = tenancy.list_repositories(conn, args.workspace)
                    return _out(args, found, "\n".join(f"{r['id']}  {r['name']:<24} {r['path']}" for r in found) or "no repositories registered")
                if not tenancy.archive_repository(conn, args.workspace, args.id):
                    raise TenancyError("no such repository")
                return _out(args, {"unregistered": True}, "unregistered")
            if args.cmd == "projects":
                if args.projects_cmd == "add":
                    pid = tenancy.create_project(conn, org, args.workspace, args.name, args.team)
                    return _out(args, {"id": pid}, f"created project {pid}")
                if args.projects_cmd == "list":
                    found = tenancy.list_projects(conn, args.workspace)
                    return _out(args, found, "\n".join(f"{p['id']}  {p['name']:<20} team={p['team_id'] or '-'}  repos={len(p['repositories'])}" for p in found)
                                or "no projects")
                tenancy.assign_repository(conn, args.workspace, args.repo_id, args.project_id)
                return _out(args, {"assigned": True}, "assigned")
            if args.teams_cmd == "add":
                tid = tenancy.create_team(conn, org, args.name)
                return _out(args, {"id": tid}, f"created team {tid}")
            if args.teams_cmd == "list":
                found = tenancy.list_teams(conn, org)
                return _out(args, found, "\n".join(f"{t['id']}  {t['name']:<20} members={len(t['members'])} roles={t['roles']}" for t in found) or "no teams")
            if args.teams_cmd == "member":
                (tenancy.add_member if args.op == "add" else tenancy.remove_member)(conn, org, args.team_id, args.principal_id)
                return _out(args, {"ok": True}, f"{args.op}ed")
            tenancy.grant_team_role(conn, org, args.team_id, args.workspace, Role(args.role))
            return _out(args, {"granted": args.role}, f"team {args.team_id} is now {args.role} in {args.workspace}")
    except (TenancyError, RepoPathError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

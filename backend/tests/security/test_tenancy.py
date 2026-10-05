"""Repositories belong to one workspace; projects can belong to teams; team roles flow to members; nothing crosses tenants."""

from __future__ import annotations

import pytest

from patchquest.application import TaskService
from patchquest.database import get_db
from patchquest.domain.identity import Role
from patchquest.domain.tenancy import ProjectAccessDenied, RepositoryClaimed, RepositoryNotRegistered
from patchquest.persistence import identity as ids
from patchquest.persistence import tenancy


def body(world, ws="a", **kw):
    return {"workspace_id": world.ws[ws], **kw}


def principal_id(world, who):
    with get_db() as conn:
        return conn.execute("SELECT principal_id FROM api_tokens WHERE id = ?", (world.token_ids[who],)).fetchone()[0]


async def register(world, who, path, ws="a", **kw):
    async with world.client(who) as c:
        return await c.post("/api/repositories", json=body(world, ws, path=path, **kw))


# ------------------------------------------------------------------ path ownership
@pytest.mark.asyncio
async def test_a_path_belongs_to_one_workspace_including_paths_inside_and_around_it(world, tmp_path):
    shared = tmp_path / "mono"
    (shared / "svc").mkdir(parents=True)
    r = await register(world, "admin_a", str(shared))
    assert r.status_code == 201
    again = await register(world, "admin_a", str(shared))
    assert again.status_code == 201 and again.json()["id"] == r.json()["id"]  # idempotent for the owner
    for path in (shared, shared / "svc", tmp_path):
        denied = await register(world, "owner_b", str(path), ws="b")
        assert denied.status_code == 409, path
    assert str(world.repos["b"]).startswith(str(tmp_path))  # b's own repo is a sibling, unaffected
    async with world.client("admin_a") as c:
        assert (await c.delete(f"/api/repositories/{r.json()['id']}", params={"workspace_id": world.ws["a"]})).status_code == 200
    assert (await register(world, "owner_b", str(shared), ws="b")).status_code == 201  # released paths can be claimed


@pytest.mark.asyncio
async def test_registering_needs_the_repository_permission_and_a_valid_path(world, tmp_path):
    assert (await register(world, "dev_a", str(tmp_path))).status_code == 403
    assert (await register(world, "viewer_a", str(tmp_path))).status_code == 403
    assert (await register(world, "admin_a", "/nonexistent/path")).status_code == 422
    assert (await register(world, "admin_a", "/etc")).status_code == 422
    assert (await register(world, "admin_a", str(tmp_path / "x\x00y"))).status_code == 422


@pytest.mark.asyncio
async def test_a_run_needs_a_registered_repository_of_its_own_workspace(world, tmp_path):
    svc = TaskService()
    (tmp_path / "unregistered").mkdir()
    for path in (tmp_path / "unregistered", world.repos["b"]):  # nobody's, and someone else's
        with pytest.raises(RepositoryNotRegistered):
            svc.create_run(repo_path=str(path), task="t", workspace_id=world.ws["a"], created_by="user:x")
    async with world.client("dev_a") as c:
        foreign = await c.post("/api/runs", json={"repo_path": world.repos["b"], "task": "read only: explain"})
        assert foreign.status_code == 403 and foreign.json()["detail"]["code"] == "repository_not_allowed"
        nested = world.repos["a"] + "/sub"
        (tmp_path / "repo-a" / "sub").mkdir()
        assert (await c.post("/api/runs", json={"repo_path": nested, "task": "read only: explain"})).status_code == 200  # inside a registered repo


@pytest.mark.asyncio
async def test_the_implicit_local_workspace_needs_no_registration(tmp_path):
    (tmp_path / "anywhere").mkdir()
    assert TaskService().create_run(repo_path=str(tmp_path / "anywhere"), task="t")["workspace_id"] == "ws_local"


def test_a_workflow_cannot_aim_an_agent_at_another_tenants_repository(world):
    from patchquest.workflows.engine import (
        WorkflowEngine,  # noqa: F401  (the engine funnels through create_run, tested here at the seam)
    )
    with pytest.raises(RepositoryNotRegistered):
        TaskService().create_run(repo_path=world.repos["b"], task="t", workspace_id=world.ws["a"], created_by="workflow:key", acting_as="user:x")


# ------------------------------------------------------------------ teams own projects
@pytest.mark.asyncio
async def test_team_owned_projects_limit_who_can_start_runs_on_their_repositories(world):
    payments = principal_id(world, "dev_a")
    with get_db() as conn:
        org = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (world.ws["a"],)).fetchone()[0]
        team = tenancy.create_team(conn, org, "payments")
        other = ids.create_principal(conn, org, "outsider")
        ids.set_role(conn, other, world.ws["a"], Role.DEVELOPER)
        _, outsider_token = ids.issue_token(conn, other)
        tenancy.add_member(conn, org, team, payments)
        project = tenancy.create_project(conn, org, world.ws["a"], "billing", team)
        repo = tenancy.list_repositories(conn, world.ws["a"])[0]
        tenancy.assign_repository(conn, world.ws["a"], repo["id"], project)
    world.tokens["outsider"] = outsider_token
    run = {"repo_path": world.repos["a"], "task": "read only: explain"}
    async with world.client("dev_a") as c:  # a team member
        assert (await c.post("/api/runs", json=run)).status_code == 200
    async with world.client("outsider") as c:  # a developer outside the team
        denied = await c.post("/api/runs", json=run)
        assert denied.status_code == 403 and "team" in denied.json()["detail"]["message"]
        assert (await c.get(f"/api/runs/{world.runs['a']}")).status_code == 200  # reading stays workspace-wide
    async with world.client("admin_a") as c:  # workspace admins are not locked out
        assert (await c.post("/api/runs", json=run)).status_code == 200
    with pytest.raises(ProjectAccessDenied):
        with get_db() as conn:
            tenancy.check_run_allowed(conn, workspace_id=world.ws["a"], repo_path=world.repos["a"], actor=f"user:{other}")
    with get_db() as conn:  # work started by the system, with no person behind it, is not blocked by the team rule
        assert tenancy.check_run_allowed(conn, workspace_id=world.ws["a"], repo_path=world.repos["a"], actor="workflow:k")


# ------------------------------------------------------------------ team roles
@pytest.mark.asyncio
async def test_a_team_role_reaches_members_and_leaves_with_them(world):
    viewer = principal_id(world, "viewer_a")
    with get_db() as conn:
        org = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (world.ws["a"],)).fetchone()[0]
    async with world.client("owner_a") as c:
        team = (await c.post("/api/teams", json=body(world, name="oncall"))).json()["id"]
        assert (await c.post(f"/api/teams/{team}/members", json=body(world, principal_id=viewer))).status_code == 200
        assert (await c.put(f"/api/teams/{team}/roles", json=body(world, role="OPERATOR"))).status_code == 200
    async with world.client("viewer_a") as c:  # direct role VIEWER, team role OPERATOR: the stronger applies
        assert (await c.post(f"/api/runs/{world.runs['a']}/cancel")).status_code == 409  # allowed (run is just not active)
    async with world.client("owner_a") as c:
        assert (await c.delete(f"/api/teams/{team}/members/{viewer}", params={"workspace_id": world.ws["a"]})).status_code == 200
    async with world.client("viewer_a") as c:
        assert (await c.post(f"/api/runs/{world.runs['a']}/cancel")).status_code == 403
    with get_db() as conn:
        assert ids.load_principal(conn, viewer).role_in(world.ws["a"]) is Role.VIEWER
        assert org


@pytest.mark.asyncio
async def test_who_may_manage_teams_and_what_roles_an_admin_may_grant(world):
    async with world.client("admin_a") as c:
        assert (await c.post("/api/teams", json=body(world, name="nope"))).status_code == 403  # teams are organisation-wide: owners only
    async with world.client("owner_a") as c:
        team = (await c.post("/api/teams", json=body(world, name="platform"))).json()["id"]
        assert (await c.post("/api/teams", json=body(world, name="platform"))).status_code == 422  # duplicate
    async with world.client("admin_a") as c:
        assert (await c.put(f"/api/teams/{team}/roles", json=body(world, role="DEVELOPER"))).status_code == 200
        assert (await c.put(f"/api/teams/{team}/roles", json=body(world, role="OWNER"))).status_code == 403  # an admin cannot mint owners
        assert (await c.put(f"/api/teams/{team}/roles", json=body(world, role="WIZARD"))).status_code == 422
    async with world.client("dev_a") as c:
        assert (await c.put(f"/api/teams/{team}/roles", json=body(world, role="VIEWER"))).status_code == 403


@pytest.mark.asyncio
async def test_teams_cannot_cross_organisations(world):
    with get_db() as conn:
        org_b = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (world.ws["b"],)).fetchone()[0]
        theirs = tenancy.create_team(conn, org_b, "theirs")
    async with world.client("owner_a") as c:
        # someone else's team looks nonexistent, whatever we try with it
        assert (await c.post(f"/api/teams/{theirs}/members", json=body(world, principal_id=principal_id(world, "dev_a")))).status_code == 422
        assert (await c.put(f"/api/teams/{theirs}/roles", json=body(world, role="VIEWER"))).status_code == 404
        assert (await c.delete(f"/api/teams/{theirs}/roles/{world.ws['a']}", params={"workspace_id": world.ws["a"]})).status_code == 404
        mine = (await c.post("/api/teams", json=body(world, name="mine"))).json()["id"]
        assert (await c.post(f"/api/teams/{mine}/members", json=body(world, principal_id=principal_id(world, "dev_b")))).status_code == 422  # b's person
        assert (await c.post("/api/projects", json=body(world, name="p", team_id=theirs))).status_code == 422
    with get_db() as conn:
        with pytest.raises(Exception, match="own organisation"):
            tenancy.grant_team_role(conn, conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (world.ws["a"],)).fetchone()[0], mine,
                                    world.ws["b"], Role.VIEWER)


# ------------------------------------------------------------------ isolation of the new objects
@pytest.mark.asyncio
async def test_another_tenant_sees_none_of_the_new_objects(world):
    async with world.client("owner_a") as c:
        team = (await c.post("/api/teams", json=body(world, name="t"))).json()["id"]
        project = (await c.post("/api/projects", json=body(world, name="p"))).json()["id"]
    repo = tenancy_repo(world, "a")
    async with world.client("owner_b") as c:
        a = world.ws["a"]
        for url in ("/api/repositories", "/api/projects", "/api/teams"):
            assert (await c.get(url, params={"workspace_id": a})).status_code == 404, url
        assert (await c.post("/api/repositories", json=body(world, path=world.repos["a"])) ).status_code == 404
        assert (await c.post("/api/projects", json=body(world, name="x"))).status_code == 404
        assert (await c.post("/api/teams", json=body(world, name="x"))).status_code == 404
        assert (await c.delete(f"/api/repositories/{repo}", params={"workspace_id": a})).status_code == 404
        # forged ids through one's own workspace are just missing
        mine = world.ws["b"]
        assert (await c.delete(f"/api/repositories/{repo}", params={"workspace_id": mine})).status_code == 404
        assert (await c.put(f"/api/repositories/{repo}/project", json={"workspace_id": mine, "project_id": None})).status_code == 404
        assert (await c.put(f"/api/projects/{project}/team", json={"workspace_id": mine, "team_id": None})).status_code == 404
        assert (await c.post(f"/api/teams/{team}/members", json={"workspace_id": mine, "principal_id": principal_id(world, "dev_b")})).status_code == 422
    with get_db() as conn:
        assert [r["id"] for r in tenancy.list_repositories(conn, world.ws["a"])] == [repo]


def tenancy_repo(world, ws):
    with get_db() as conn:
        return tenancy.list_repositories(conn, world.ws[ws])[0]["id"]


def test_claim_errors_are_typed(world):
    with get_db() as conn:
        with pytest.raises(RepositoryClaimed):
            tenancy.register_repository(conn, world.ws["a"], world.repos["b"], None, None, "x")


def test_cli_sets_up_ownership_and_refuses_double_claims(world, tmp_path, capsys):
    import json

    from patchquest.cli import main

    def run(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    code, out, _ = run("repos", "add", str(fresh), "--workspace", world.ws["a"], "--json")
    assert code == 0 and json.loads(out)["path"] == str(fresh.resolve())
    code, _, err = run("repos", "add", str(fresh), "--workspace", world.ws["b"])
    assert code == 1 and "another workspace" in err
    code, out, _ = run("teams", "add", "qa", "--workspace", world.ws["a"], "--json")
    team = json.loads(out)["id"]
    assert run("teams", "grant", team, "OPERATOR", "--workspace", world.ws["a"])[0] == 0
    assert run("teams", "grant", team, "OPERATOR", "--workspace", world.ws["b"])[0] == 1  # b is in another organisation
    code, out, _ = run("projects", "add", "core", "--team", team, "--workspace", world.ws["a"], "--json")
    assert code == 0 and run("projects", "list", "--workspace", world.ws["a"])[1].count("core") == 1
    assert run("repos", "list", "--workspace", "ws_nope")[0] == 1


@pytest.mark.asyncio
async def test_me_lists_only_the_callers_workspaces_and_their_permissions(world):
    async with world.client("dev_a") as c:
        body = (await c.get("/api/me")).json()
    assert [w["id"] for w in body["workspaces"]] == [world.ws["a"]] and body["workspaces"][0]["role"] == "DEVELOPER"
    assert "run.create" in body["workspaces"][0]["permissions"] and "connector.manage" not in body["workspaces"][0]["permissions"]
    async with world.client() as c:
        assert (await c.get("/api/me")).status_code == 401


@pytest.mark.asyncio
async def test_a_directory_that_holds_other_repositories_cannot_be_claimed(world, tmp_path):
    """Claiming /srv/repos would hand a tenant every checkout beneath it and lock the others out of registering theirs."""
    import subprocess

    parent = tmp_path / "all-repos"
    other = parent / "someone-elses"
    other.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    refused = await register(world, "admin_a", str(parent))
    assert refused.status_code == 422 and "contains other repositories" in refused.json()["detail"]["message"]
    ok = await register(world, "admin_a", str(other))  # the repository itself is fine
    assert ok.status_code == 201
    deep = parent / "a" / "b" / "c" / "d"
    (deep / ".git").mkdir(parents=True)
    assert tenancy.check_registrable(str(deep)) is None  # its own root, however deep
    with get_db() as conn:
        assert [r["path"] for r in tenancy.list_repositories(conn, world.ws["a"]) if "all-repos" in r["path"]] == [str(other.resolve())]

"""Policy is administered per workspace: roles gate who may change it, and tenants cannot see or affect each other's."""

from __future__ import annotations

import pytest

from patchquest.database import get_db
from patchquest.domain.policy import Scope
from patchquest.persistence import identity, policies

DENY_ALL = {"name": "lockdown", "scope": "workspace", "rules": [{"action": "*", "result": "DENY", "reason": "frozen"}]}


def body(world, who="a", doc=None, **extra):
    return {"workspace_id": world.ws[who], "document": doc or DENY_ALL, **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["owner_a", "admin_a"])
async def test_owners_and_admins_set_policy_and_it_is_audited(world, who):
    async with world.client(who) as c:
        r = await c.put("/api/policies", json=body(world))
        assert r.status_code == 200 and r.json()["version"] == 1
        listed = await c.get("/api/policies", params={"workspace_id": world.ws["a"]})
    assert [p["name"] for p in listed.json()] == ["lockdown"]
    with get_db() as conn:
        assert any(e["action"] == "policy.put" for e in identity.read_audit(conn, None))


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["dev_a", "ops_a", "viewer_a", "ci_a"])
async def test_other_roles_cannot_change_policy(world, who):
    async with world.client(who) as c:
        assert (await c.put("/api/policies", json=body(world))).status_code == 403
    with get_db() as conn:
        assert policies.list_policies(conn) == []


@pytest.mark.asyncio
async def test_another_tenant_sees_not_found_for_every_policy_route(world):
    async with world.client("owner_a") as c:
        await c.put("/api/policies", json=body(world))
    async with world.client("owner_b") as c:
        w = world.ws["a"]
        assert (await c.get("/api/policies", params={"workspace_id": w})).status_code == 404
        assert (await c.put("/api/policies", json=body(world, "a"))).status_code == 404
        assert (await c.post("/api/policies/explain", json={"workspace_id": w, "action": "x"})).status_code == 404
        assert (await c.delete("/api/policies/workspace/lockdown", params={"workspace_id": w})).status_code == 404
    with get_db() as conn:
        assert len(policies.list_policies(conn, Scope.WORKSPACE, world.ws["a"])) == 1


@pytest.mark.asyncio
async def test_a_policy_in_one_workspace_never_applies_in_another(world):
    async with world.client("owner_a") as c:
        await c.put("/api/policies", json=body(world))
    async with world.client("owner_b") as c:
        d = (await c.post("/api/policies/explain", json={"workspace_id": world.ws["b"], "action": "command.run", "effect": "READ_ONLY"})).json()
    assert d["result"] == "ALLOW"


@pytest.mark.asyncio
async def test_organisation_policy_needs_an_owner_and_repository_scope_is_cli_only(world):
    org_doc = {**DENY_ALL, "name": "corp", "scope": "organization"}
    async with world.client("admin_a") as c:
        assert (await c.put("/api/policies", json=body(world, doc=org_doc))).status_code == 403
        assert (await c.put("/api/policies", json=body(world, doc={**DENY_ALL, "scope": "repository"}))).status_code == 422
    async with world.client("owner_a") as c:
        assert (await c.put("/api/policies", json=body(world, doc=org_doc))).status_code == 200


@pytest.mark.asyncio
async def test_a_workflow_policy_needs_a_workflow_in_the_callers_workspace(world):
    doc = {**DENY_ALL, "scope": "workflow"}
    async with world.client("owner_a") as c:
        assert (await c.put("/api/policies", json=body(world, doc=doc, ref="not-mine"))).status_code == 404


@pytest.mark.asyncio
async def test_a_malformed_document_is_a_422_that_stores_nothing(world):
    async with world.client("owner_a") as c:
        r = await c.put("/api/policies", json=body(world, doc={"name": "x", "scope": "workspace", "rules": [{"action": "a", "result": "??"}]}))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_policy"
    with get_db() as conn:
        assert policies.list_policies(conn) == []

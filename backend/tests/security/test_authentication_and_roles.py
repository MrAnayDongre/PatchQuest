"""Who you are (tokens) and what you may do (roles), enforced at the API."""

from __future__ import annotations

import time

import pytest

from patchquest.database import get_db
from patchquest.persistence import identity as ids


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/runs", "/api/providers", "/api/runs/x"])
async def test_without_a_token_nothing_is_reachable_once_tokens_exist(world, path):
    async with world.client() as c:
        r = await c.get(path)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_health_stays_open(world):
    async with world.client() as c:
        assert (await c.get("/api/health")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["nonsense", "pq_" + "a" * 43, "", "' OR '1'='1"])
async def test_wrong_tokens_are_refused_and_audited_without_the_token(world, bad):
    async with world.client(Authorization=f"Bearer {bad}") as c:
        r = await c.get("/api/runs")
    assert r.status_code == 401
    with get_db() as conn:
        entries = ids.read_audit(conn, None)
    assert any(e["action"] == "auth.failed" for e in entries)
    assert bad not in str(entries) or bad == ""  # the presented secret is never written to the log


@pytest.mark.asyncio
async def test_revoking_a_token_takes_effect_on_the_next_request(world):
    async with world.client("dev_a") as c:
        assert (await c.get("/api/runs")).status_code == 200
        with get_db() as conn:
            ids.revoke_token(conn, world.token_ids["dev_a"])
        assert (await c.get("/api/runs")).status_code == 401


@pytest.mark.asyncio
async def test_expired_tokens_are_refused(world):
    with get_db() as conn:
        pid = conn.execute("SELECT id FROM principals WHERE name = 'dev_a'").fetchone()[0]
        _, short = ids.issue_token(conn, pid, "short", expires_in_days=0.000001)
    time.sleep(0.2)
    async with world.client(Authorization=f"Bearer {short}") as c:
        assert (await c.get("/api/runs")).status_code == 401


@pytest.mark.asyncio
async def test_tokens_in_the_query_string_work_only_for_event_streams(world):
    token = world.tokens["viewer_a"]
    async with world.client() as c:
        assert (await c.get(f"/api/runs?token={token}")).status_code == 401  # would otherwise leak into access logs everywhere
        assert (await c.get(f"/api/runs/{world.runs['a']}/events?token={token}")).status_code == 401


@pytest.mark.asyncio
async def test_a_disabled_person_loses_access_everywhere_at_once(world):
    async with world.client("dev_a") as c:
        with get_db() as conn:
            conn.execute("UPDATE principals SET disabled = 1 WHERE name = 'dev_a'")
        assert (await c.get("/api/runs")).status_code == 401


@pytest.mark.asyncio
async def test_the_legacy_shared_token_still_works_as_the_local_owner(world, monkeypatch):
    from patchquest.config import get_config

    monkeypatch.setenv(get_config().api_token_env, "legacy-shared-secret")
    async with world.client(Authorization="Bearer legacy-shared-secret") as c:
        r = await c.get("/api/runs")
    assert r.status_code == 200 and r.json() == []  # it is the *local* owner, so it sees only the local workspace


class TestRoles:
    @pytest.mark.asyncio
    async def test_viewers_read_but_cannot_control_or_create(self, world, tmp_path):
        async with world.client("viewer_a") as c:
            assert (await c.get(f"/api/runs/{world.runs['a']}")).status_code == 200
            cancel = await c.post(f"/api/runs/{world.runs['a']}/cancel")
            create = await c.post("/api/runs", json={"repo_path": str(tmp_path / "repo"), "task": "t"})
            decide = await c.post(f"/api/runs/{world.runs['a']}/approvals/x", json={"decision": "DENY"})
        assert (cancel.status_code, create.status_code, decide.status_code) == (403, 403, 403)

    @pytest.mark.asyncio
    async def test_service_accounts_cannot_approve_what_the_agent_wants_to_do(self, world):
        async with world.client("ci_a") as c:
            assert (await c.get(f"/api/runs/{world.runs['a']}")).status_code == 200
            r = await c.post(f"/api/runs/{world.runs['a']}/approvals/x", json={"decision": "APPROVE_ONCE"})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_operators_can_cancel_and_decide_but_not_create(self, world, tmp_path):
        async with world.client("ops_a") as c:
            assert (await c.post(f"/api/runs/{world.runs['a']}/cancel")).status_code == 409  # allowed; the run is just not active
            assert (await c.post(f"/api/runs/{world.runs['a']}/approvals/x", json={"decision": "DENY"})).status_code == 404  # allowed; no such approval
            assert (await c.post("/api/runs", json={"repo_path": str(tmp_path / "repo"), "task": "t"})).status_code == 403

    @pytest.mark.asyncio
    async def test_developers_can_do_everything_to_their_own_runs(self, world):
        async with world.client("dev_a") as c:
            assert (await c.get(f"/api/runs/{world.runs['a']}")).status_code == 200
            assert (await c.post(f"/api/runs/{world.runs['a']}/replay", json={"mode": "state"})).status_code == 200
            assert (await c.get(f"/api/runs/{world.runs['a']}/checkpoints")).status_code == 200

    @pytest.mark.asyncio
    async def test_a_permitted_role_in_one_workspace_grants_nothing_in_another(self, world):
        with get_db() as conn:  # make dev_a a viewer of workspace b
            pid = conn.execute("SELECT id FROM principals WHERE name = 'dev_a'").fetchone()[0]
            org_b = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (world.ws["b"],)).fetchone()[0]
            org_a = conn.execute("SELECT org_id FROM principals WHERE id = ?", (pid,)).fetchone()[0]
        assert org_a != org_b  # and the model refuses cross-organisation membership outright
        with get_db() as conn, pytest.raises(ValueError, match="own organisation"):
            ids.set_role(conn, pid, world.ws["b"], __import__("patchquest.domain.identity", fromlist=["Role"]).Role.VIEWER)

    @pytest.mark.asyncio
    async def test_decisions_and_runs_are_attributed_to_the_caller(self, world, tmp_path):
        async with world.client("dev_a") as c:
            r = await c.post("/api/runs", json={"repo_path": world.repos["a"], "task": "read only: explain the repo"})
        run_id = r.json()["id"]
        with get_db() as conn:
            row = conn.execute("SELECT created_by FROM runs WHERE id = ?", (run_id,)).fetchone()
            audit = ids.read_audit(conn, [world.ws["a"]])
        assert row["created_by"].startswith("user:")
        assert any(e["action"] == "run.create" and e["target"] == run_id and e["actor"] == row["created_by"] for e in audit)


class TestLocalMode:
    @pytest.mark.asyncio
    async def test_with_no_tokens_configured_the_caller_is_the_local_owner(self, tmp_path):
        import httpx

        from patchquest.main import app

        repo = tmp_path / "r"
        repo.mkdir()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            r = await c.post("/api/runs", json={"repo_path": str(repo), "task": "read only: explain the repo"})
            assert r.status_code == 200 and r.json()["workspace_id"] == "ws_local"
            assert (await c.get("/api/runs")).status_code == 200

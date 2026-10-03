"""Who may connect integrations, that secrets never come back out, and that tenants cannot see or touch each other's."""

from __future__ import annotations

import pytest

from patchquest import secrets_store
from patchquest.database import get_db


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("PATCHQUEST_SECRET_KEY", secrets_store.generate_key())


def slack_body(world, ws="a"):
    return {"workspace_id": world.ws[ws], "kind": "slack", "name": "slack", "config": {"channels": ["C0123ABC"]},
            "secrets": {"bot_token": {"value": "xoxb-very-secret"}, "signing_secret": {"value": "signing-very-secret"}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("who,expected", [("owner_a", 201), ("admin_a", 201), ("dev_a", 403), ("ops_a", 403), ("viewer_a", 403), ("ci_a", 403)])
async def test_only_admins_connect_integrations(world, who, expected):
    async with world.client(who) as c:
        r = await c.post("/api/integrations", json=slack_body(world))
    assert r.status_code == expected, r.text


@pytest.mark.asyncio
async def test_secrets_are_never_returned_by_any_route(world):
    async with world.client("admin_a") as c:
        created = await c.post("/api/integrations", json=slack_body(world))
        listed = await c.get("/api/integrations", params={"workspace_id": world.ws["a"]})
        tested = await c.post(f"/api/integrations/{created.json()['id']}/test", params={"workspace_id": world.ws["a"]})
        events = await c.get(f"/api/integrations/{created.json()['id']}/events", params={"workspace_id": world.ws["a"]})
    for response in (created, listed, tested, events):
        assert "very-secret" not in response.text
    view = created.json()
    assert view["secrets"] == {"bot_token": {"stored": True}, "signing_secret": {"stored": True}} and view["webhook_path"] == f"/hooks/{view['id']}"
    with get_db() as conn:
        assert "very-secret" not in str([tuple(r) for r in conn.execute("SELECT * FROM secrets")])  # not even in the database
        assert "very-secret" not in str([tuple(r) for r in conn.execute("SELECT * FROM audit_log")])


@pytest.mark.asyncio
async def test_every_role_that_can_read_settings_can_list_but_nobody_else_can(world):
    async with world.client("admin_a") as c:
        await c.post("/api/integrations", json=slack_body(world))
    for who in ("viewer_a", "dev_a", "ops_a"):
        async with world.client(who) as c:
            assert len((await c.get("/api/integrations", params={"workspace_id": world.ws["a"]})).json()) == 1
    async with world.client("ci_a") as c:  # service accounts have no settings access
        assert (await c.get("/api/integrations", params={"workspace_id": world.ws["a"]})).status_code == 403


@pytest.mark.asyncio
async def test_another_tenant_sees_nothing_and_cannot_change_anything(world):
    async with world.client("admin_a") as c:
        mine = (await c.post("/api/integrations", json=slack_body(world))).json()["id"]
    async with world.client("owner_b") as c:
        a, b = world.ws["a"], world.ws["b"]
        assert (await c.get("/api/integrations", params={"workspace_id": a})).status_code == 404
        assert (await c.post("/api/integrations", json=slack_body(world))).status_code == 404
        for method, url, extra in (("PUT", f"/api/integrations/{mine}", {"json": {"workspace_id": b, "enabled": False}}),
                                   ("DELETE", f"/api/integrations/{mine}", {"params": {"workspace_id": b}}),
                                   ("POST", f"/api/integrations/{mine}/test", {"params": {"workspace_id": b}}),
                                   ("GET", f"/api/integrations/{mine}/events", {"params": {"workspace_id": b}})):
            assert (await c.request(method, url, **extra)).status_code == 404, (method, url)
        # the other tenant can connect their own Slack with the same channel id; the two do not interact
        assert (await c.post("/api/integrations", json=slack_body(world, "b"))).status_code == 201
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM integrations WHERE workspace_id = ?", (world.ws["a"],)).fetchone()[0] == 1
        assert conn.execute("SELECT status FROM integrations WHERE id = ?", (mine,)).fetchone()[0] == "connected"


@pytest.mark.asyncio
async def test_update_rotates_a_secret_and_disables_without_losing_the_rest(world):
    async with world.client("admin_a") as c:
        made = (await c.post("/api/integrations", json=slack_body(world))).json()["id"]
        r = await c.put(f"/api/integrations/{made}", json={"workspace_id": world.ws["a"], "secrets": {"bot_token": {"env": "SLACK_BOT"}}, "enabled": False})
        assert r.status_code == 200
        view = r.json()
    assert view["status"] == "disabled" and view["secrets"]["bot_token"] == {"env": "SLACK_BOT"} and view["secrets"]["signing_secret"] == {"stored": True}
    with get_db() as conn:  # switching a secret to an env reference removes the stored value
        assert [r["name"] for r in conn.execute("SELECT name FROM secrets")] == ["signing_secret"]


@pytest.mark.asyncio
async def test_catalogue_and_validation_errors_are_helpful(world):
    async with world.client("viewer_a") as c:
        body = (await c.get("/api/integrations/kinds")).json()
    assert body["stored_secrets_available"] is True and {k["kind"] for k in body["kinds"]} == {"github", "slack", "linear", "jira", "notion", "webhook"}
    slack = next(k for k in body["kinds"] if k["kind"] == "slack")
    assert [s["name"] for s in slack["secrets"]] == ["bot_token", "signing_secret"] and slack["actions"][0]["side_effect"] == "EXTERNAL_WRITE"
    async with world.client("admin_a") as c:
        bad = await c.post("/api/integrations", json={**slack_body(world), "config": {"channels": ["general"]}})
    assert bad.status_code == 422 and "channels" in bad.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_without_an_encryption_key_stored_secrets_are_refused_with_the_fix(world, monkeypatch):
    monkeypatch.delenv("PATCHQUEST_SECRET_KEY")
    async with world.client("admin_a") as c:
        r = await c.post("/api/integrations", json=slack_body(world))
        assert r.status_code == 422 and "patchquest secrets keygen" in r.json()["detail"]["message"]
        assert (await c.get("/api/integrations/kinds")).json()["stored_secrets_available"] is False
        env_only = {**slack_body(world), "secrets": {"bot_token": {"env": "A_B"}, "signing_secret": {"env": "C_D"}}}
        assert (await c.post("/api/integrations", json=env_only)).status_code == 201


def test_cli_connects_lists_tests_and_removes(world, tmp_path, capsys, monkeypatch):
    import json

    from patchquest.cli import main

    def run(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    ws = world.ws["a"]
    code, out, _ = run("integrations", "add", "webhook", "--workspace", ws, "--set", 'event_types=["deploy.finished"]', "--secret-env", "signing_secret=DEPLOY_SECRET")
    assert code == 0 and "/hooks/int_" in out
    code, out, _ = run("integrations", "list", "--workspace", ws, "--json")
    [row] = json.loads(out)
    assert row["kind"] == "webhook" and row["secrets"] == {"signing_secret": {"env": "DEPLOY_SECRET"}}
    code, out, _ = run("integrations", "test", row["id"], "--workspace", ws)
    assert code == 0 and "configuration is valid" in out  # nothing to call for a plain webhook
    assert run("integrations", "add", "slack", "--workspace", ws, "--set", 'channels=["general"]')[0] == 1
    assert run("integrations", "remove", row["id"], "--workspace", ws)[0] == 0
    assert "no integrations" in run("integrations", "list", "--workspace", ws)[1]
    code, out, _ = run("secrets", "keygen")
    assert code == 0 and len(out.strip()) == 44
    assert run("integrations", "kinds", "--json")[0] == 0


def test_doctor_flags_stored_secrets_without_the_key(world, monkeypatch):
    from patchquest.doctor import check_secrets

    with get_db() as conn:
        from patchquest.integrations import service
        service.create(conn, world.ws["a"], "webhook", "w", {"event_types": ["x"]}, {"signing_secret": {"value": "s3cret"}}, "user:a")
    assert check_secrets().status == "ok"
    monkeypatch.delenv("PATCHQUEST_SECRET_KEY")
    bad = check_secrets()
    assert bad.status == "fail" and "PATCHQUEST_SECRET_KEY" in bad.fix

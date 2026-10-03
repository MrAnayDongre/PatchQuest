"""Integrations end to end: configure, receive signed webhooks, start workflows, act with the workspace's own credentials.

MOCKED_PROTOCOL_TEST: every external service here is a simulator.
"""

from __future__ import annotations

import time

import httpx
import pytest

from patchquest import secrets_store
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import (
    MockGitHub,
    MockLinear,
    MockSlack,
    github_delivery,
    slack_delivery,
    webhook_delivery,
)
from patchquest.database import get_db
from patchquest.domain.workflows import parse
from patchquest.integrations import service
from patchquest.workflows import store
from patchquest.workflows.runtime import get_engine
from tests.unit.connectors.conftest import resolver

pytestmark = pytest.mark.mocked_protocol
CHANNEL = "C0123ABC"
GH_SECRET = "gh-hook-secret"
SLACK_SIGNING = "slack-signing"


class Network:
    """One transport that sends each request to the simulator for its host."""

    def __init__(self) -> None:
        self.github = MockGitHub("acme/widgets", "gh-token")
        self.slack = MockSlack("xoxb-token", [CHANNEL])
        self.linear = MockLinear("lin-key", ["iss-1"])
        self.hosts = {"api.github.com": self.github, "slack.com": self.slack, "api.linear.app": self.linear}

    def handler(self, request: httpx.Request) -> httpx.Response:
        return self.hosts[request.url.host].handler(request)


@pytest.fixture
def net(monkeypatch):
    monkeypatch.setenv("PATCHQUEST_SECRET_KEY", secrets_store.generate_key())
    network = Network()
    service.set_http(SafeHttp(transport=httpx.MockTransport(network.handler), resolve=resolver()))
    yield network
    service.set_http(None)


@pytest.fixture
def ws(world):
    return world.ws["a"]


def connect_github(ws, **kw):
    with get_db() as conn:
        return service.create(conn, ws, "github", "acme widgets", {"repo": "acme/widgets"},
                              {"token": {"value": "gh-token"}, "webhook_secret": {"value": GH_SECRET}}, "user:admin", **kw)


def connect_slack(ws):
    with get_db() as conn:
        return service.create(conn, ws, "slack", "team slack", {"channels": [CHANNEL]},
                              {"bot_token": {"value": "xoxb-token"}, "signing_secret": {"value": SLACK_SIGNING}}, "user:admin")


def save_flow(ws, raw):
    wf = parse(raw)
    assert get_engine().check(wf) == []
    with get_db() as conn:
        return store.save_version(conn, ws, wf, "user:admin")[0]


async def post_hook(client, integration_id, delivery):
    return await client.post(f"/hooks/{integration_id}", content=delivery.body, headers=delivery.headers)


def labeled_issue(number=7, delivery="gh-1"):
    payload = {"action": "labeled", "repository": {"full_name": "acme/widgets"}, "sender": {"login": "ana"},
               "issue": {"number": number, "title": "Crash on save", "body": "Steps...", "labels": [{"name": "agent-ready"}]}, "label": {"name": "agent-ready"}}
    return github_delivery(GH_SECRET.encode(), "issues", payload, delivery)


def slack_mention(clock_now, event_id="Ev1", text="deploy status?"):
    return slack_delivery(SLACK_SIGNING.encode(), {"type": "event_callback", "team_id": "T1", "event_id": event_id, "event_time": int(clock_now),
                                                   "event": {"type": "app_mention", "user": "U1", "text": text, "ts": "1760000000.000100", "channel": CHANNEL}},
                          int(clock_now))


# ------------------------------------------------------------------ the full path
@pytest.mark.asyncio
async def test_a_github_issue_starts_a_workflow_that_comments_and_notifies_slack_after_approval(world, ws, net):
    connect_github(ws)
    connect_slack(ws)
    issue = net.github.add_issue("Crash on save")
    save_flow(ws, {"name": "triage", "trigger": {"type": "github.issues.labeled", "filter": {"payload.label": "agent-ready"}}, "nodes": [
        {"id": "gate", "type": "approval", "config": {"message": "Reply on the issue and tell the team?"}},
        {"id": "comment", "type": "action", "config": {"action": "github.comment", "params": {"issue_number": "{{trigger.payload.number}}",
                                                                                            "body": "On it: {{trigger.payload.title}}"}}},
        {"id": "tell", "type": "action", "config": {"action": "slack.post_message", "params": {"channel": CHANNEL,
                                                                                            "text": "Looking at #{{trigger.payload.number}}"}}},
        {"id": "done", "type": "end", "config": {}}],
        "edges": [{"from": "gate", "to": "comment", "when": "approved"}, {"from": "gate", "to": "done", "when": "denied"},
                  {"from": "comment", "to": "tell"}, {"from": "tell", "to": "done"}]})
    integration = service.find_enabled(ws, "github")["id"]
    async with world.client() as c:
        accepted = await post_hook(c, integration, labeled_issue(issue))
        assert accepted.status_code == 202 and accepted.json() == {"status": "accepted", "workflows": 1}
        assert (await post_hook(c, integration, labeled_issue(issue))).json()["status"] == "duplicate"  # GitHub retries: handled once
    assert net.github.all_comment_bodies() == [] and net.slack.texts() == []  # nothing external before a person approves

    with get_db() as conn:
        run = conn.execute("SELECT id, workspace_id FROM workflow_runs").fetchone()
    assert run["workspace_id"] == ws
    await get_engine().decide(run["id"], "gate", "approve", "user:ana")
    for _ in range(50):
        await get_engine().tick()
        with get_db() as conn:
            if store.get_run(conn, run["id"])["status"] == "completed":
                break
        time.sleep(0.02)
    [comment] = net.github.all_comment_bodies()
    assert comment.startswith("On it: Crash on save") and "patchquest:idempotency:" in comment
    assert net.slack.texts() == [f"Looking at #{issue}"]
    with get_db() as conn:
        assert conn.execute("SELECT status, run_id FROM connector_events WHERE source = 'github'").fetchone()["status"] == "STARTED"


@pytest.mark.asyncio
async def test_without_an_integration_the_action_fails_with_a_clear_reason(world, ws, net):
    save_flow(ws, {"name": "n", "trigger": {"type": "manual"}, "nodes": [
        {"id": "gate", "type": "approval", "config": {}},
        {"id": "tell", "type": "action", "config": {"action": "slack.post_message", "params": {"channel": CHANNEL, "text": "x"}}},
        {"id": "done", "type": "end", "config": {}}], "edges": [{"from": "gate", "to": "tell", "when": "approved"}, {"from": "tell", "to": "done"}]})
    with get_db() as conn:
        wf_id = conn.execute("SELECT id FROM workflows").fetchone()["id"]
    run_id = get_engine().start(wf_id, {"type": "manual"})
    await get_engine().advance(run_id)
    await get_engine().decide(run_id, "gate", "approve", "user:ana")
    await get_engine().advance(run_id)
    with get_db() as conn:
        step = next(s for s in store.steps(conn, run_id) if s["node_id"] == "tell")
    assert step["status"] == "failed" and "no slack connector is connected" in step["error"]


@pytest.mark.asyncio
async def test_one_workspaces_integration_is_never_used_by_another(world, net):
    connect_slack(world.ws["a"])
    save_flow(world.ws["b"], {"name": "n", "trigger": {"type": "manual"}, "nodes": [
        {"id": "gate", "type": "approval", "config": {}},
        {"id": "tell", "type": "action", "config": {"action": "slack.post_message", "params": {"channel": CHANNEL, "text": "x"}}},
        {"id": "done", "type": "end", "config": {}}], "edges": [{"from": "gate", "to": "tell", "when": "approved"}, {"from": "tell", "to": "done"}]})
    with get_db() as conn:
        wf_id = conn.execute("SELECT id FROM workflows WHERE workspace_id = ?", (world.ws["b"],)).fetchone()["id"]
    run_id = get_engine().start(wf_id, {"type": "manual"})
    await get_engine().advance(run_id)
    await get_engine().decide(run_id, "gate", "approve", "user:ana")
    await get_engine().advance(run_id)
    assert net.slack.texts() == []  # b has no Slack; a's token was not borrowed


# ------------------------------------------------------------------ ingress behaviour
@pytest.mark.asyncio
async def test_slack_url_verification_is_answered_only_for_a_correctly_signed_request(world, ws, net):
    integration = connect_slack(ws)
    now = time.time()
    good = slack_delivery(SLACK_SIGNING.encode(), {"type": "url_verification", "challenge": "xyz"}, int(now))
    forged = slack_delivery(b"nope", {"type": "url_verification", "challenge": "xyz"}, int(now))
    async with world.client() as c:
        reply = await post_hook(c, integration, good)
        assert reply.status_code == 200 and reply.json() == {"challenge": "xyz"}
        assert (await post_hook(c, integration, forged)).status_code == 401


@pytest.mark.asyncio
async def test_bad_deliveries_are_refused_without_effect_and_the_attempts_are_audited(world, ws, net):
    integration = connect_github(ws)
    net.github.add_issue()
    save_flow(ws, {"name": "w", "trigger": {"type": "github.issues.labeled"}, "nodes": [{"id": "done", "type": "end", "config": {}}], "edges": []})
    forged = github_delivery(b"wrong", "issues", {"action": "labeled"}, "d1")
    unsigned = labeled_issue()
    unsigned.headers.pop("X-Hub-Signature-256")
    async with world.client() as c:
        assert (await post_hook(c, integration, forged)).status_code == 401
        assert (await post_hook(c, integration, unsigned)).status_code == 401
        assert (await c.post(f"/hooks/{integration}", content=b"x" * (1 << 20 + 1), headers=labeled_issue().headers)).status_code == 413
        assert (await c.post("/hooks/int_unknown", content=b"{}")).status_code == 404
        other_repo = github_delivery(GH_SECRET.encode(), "issues", {"action": "labeled", "repository": {"full_name": "evil/repo"}, "sender": {"login": "x"},
                                                                     "issue": {"number": 1, "title": "t", "labels": []}, "label": {"name": "x"}}, "d2")
        ignored = await post_hook(c, integration, other_repo)
        assert ignored.status_code == 200 and ignored.json() == {"status": "ignored"}
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0 and conn.execute("SELECT COUNT(*) FROM connector_events").fetchone()[0] == 0
        audited = [r["detail_json"] for r in conn.execute("SELECT detail_json FROM audit_log WHERE action = 'webhook.rejected'")]
    assert len(audited) == 2


@pytest.mark.asyncio
async def test_a_disabled_integration_and_one_without_an_inbound_side_do_not_answer(world, ws, net):
    integration = connect_github(ws)
    with get_db() as conn:
        service.update(conn, ws, integration, "user:admin", enabled=False)
        notion = service.create(conn, ws, "notion", "wiki", {"page_ids": ["a" * 32]}, {"token": {"env": "NOTION_TOKEN"}}, "user:admin")
    async with world.client() as c:
        assert (await post_hook(c, integration, labeled_issue())).status_code == 404
        assert (await c.post(f"/hooks/{notion}", content=b"{}")).status_code == 404


@pytest.mark.asyncio
async def test_a_flood_is_cut_off(world, ws, net, monkeypatch):
    from patchquest.api import routes_hooks
    monkeypatch.setattr(routes_hooks, "_LIMIT", 5)
    routes_hooks._hits.clear()
    integration = connect_github(ws)
    async with world.client() as c:
        codes = [(await c.post(f"/hooks/{integration}", content=b"{}")).status_code for _ in range(8)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429] * 3


@pytest.mark.asyncio
async def test_generic_webhooks_start_workflows_by_event_type_and_carry_their_payload(world, ws, net):
    with get_db() as conn:
        integration = service.create(conn, ws, "webhook", "deploys", {"event_types": ["deploy.finished"]}, {"signing_secret": {"value": "deploy-secret"}}, "user:admin")
    save_flow(ws, {"name": "after-deploy", "trigger": {"type": "webhook.deploy.finished", "filter": {"payload.env": "prod"}},
                   "nodes": [{"id": "done", "type": "end", "config": {}}], "edges": []})
    now = int(time.time())
    async with world.client() as c:
        prod = webhook_delivery(b"deploy-secret", "deploy.finished", {"env": "prod", "service": "api"}, "d1", now)
        staging = webhook_delivery(b"deploy-secret", "deploy.finished", {"env": "staging"}, "d2", now)
        unlisted = webhook_delivery(b"deploy-secret", "user.deleted", {}, "d3", now)
        assert (await post_hook(c, integration, prod)).json() == {"status": "accepted", "workflows": 1}
        assert (await post_hook(c, integration, staging)).json() == {"status": "accepted", "workflows": 0}  # accepted, but the filter did not match
        assert (await post_hook(c, integration, unlisted)).json() == {"status": "ignored"}
        stale = webhook_delivery(b"deploy-secret", "deploy.finished", {"env": "prod"}, "d4", now - 600)
        assert (await post_hook(c, integration, stale)).status_code == 401  # replayed after the window


# ------------------------------------------------------------------ the service itself
def test_secrets_are_encrypted_at_rest_write_only_and_bound_to_their_row(world, ws, net):
    integration = connect_github(ws)
    with get_db() as conn:
        raw = conn.execute("SELECT ciphertext FROM secrets WHERE owner_id = ? AND name = 'token'", (integration,)).fetchone()["ciphertext"]
        assert "gh-token" not in raw
        view = service.public_view(service.get(conn, ws, integration))
        assert view["secrets"] == {"token": {"stored": True}, "webhook_secret": {"stored": True}} and "gh-token" not in str(view)
        assert secrets_store.get(conn, ws, integration, "token") == "gh-token"
        conn.execute("UPDATE secrets SET ciphertext = (SELECT ciphertext FROM secrets WHERE name = 'token') WHERE name = 'webhook_secret'")  # a copied ciphertext
        with pytest.raises(secrets_store.SecretsUnavailable, match="does not belong"):
            secrets_store.get(conn, ws, integration, "webhook_secret")


def test_storing_secrets_needs_a_key_but_environment_references_always_work(world, ws, monkeypatch):
    monkeypatch.delenv("PATCHQUEST_SECRET_KEY", raising=False)
    with get_db() as conn:
        with pytest.raises(service.IntegrationError, match="PATCHQUEST_SECRET_KEY"):
            service.create(conn, ws, "linear", "lin", {}, {"api_key": {"value": "k"}, "webhook_secret": {"value": "w"}}, "user:a")
        made = service.create(conn, ws, "linear", "lin", {}, {"api_key": {"env": "LIN_KEY"}, "webhook_secret": {"env": "LIN_WH"}}, "user:a")
        assert service.public_view(service.get(conn, ws, made))["secrets"]["api_key"] == {"env": "LIN_KEY"}


def test_rotating_a_key_keeps_old_secrets_readable(world, ws, monkeypatch):
    old = secrets_store.generate_key()
    monkeypatch.setenv("PATCHQUEST_SECRET_KEY", old)
    integration = connect_github(ws)
    new = secrets_store.generate_key()
    monkeypatch.setenv("PATCHQUEST_SECRET_KEY", new)
    monkeypatch.setenv("PATCHQUEST_SECRET_KEY_PREVIOUS", old)
    with get_db() as conn:
        assert secrets_store.get(conn, ws, integration, "token") == "gh-token"
    monkeypatch.delenv("PATCHQUEST_SECRET_KEY_PREVIOUS")
    with get_db() as conn, pytest.raises(secrets_store.SecretsUnavailable, match="cannot be decrypted"):
        secrets_store.get(conn, ws, integration, "token")


def test_validation_refuses_bad_configuration_and_a_second_integration_of_a_kind(world, ws, net):
    connect_github(ws)
    with get_db() as conn:
        for kind, config, secrets, why in (
                ("github", {"repo": "acme/other"}, {"token": {"env": "A"}, "webhook_secret": {"env": "B"}}, "already has"),
                ("slack", {"channels": ["general"]}, {"bot_token": {"env": "A"}, "signing_secret": {"env": "B"}}, "channels"),
                ("slack", {"channels": [CHANNEL], "extra": 1}, {"bot_token": {"env": "A"}, "signing_secret": {"env": "B"}}, "unknown setting"),
                ("slack", {"channels": [CHANNEL]}, {"bot_token": {"env": "A"}}, "missing secret"),
                ("slack", {"channels": [CHANNEL]}, {"bot_token": {"env": "not valid!"}, "signing_secret": {"env": "B"}}, "environment variable"),
                ("slack", {"channels": [CHANNEL]}, {"bot_token": "plain", "signing_secret": {"env": "B"}}, "must be"),
                ("jira", {"base_url": "http://insecure.example", "project_key": "OPS"}, {"email": {"env": "A"}, "api_token": {"env": "B"}, "webhook_secret": {"env": "C"}}, "base_url"),
                ("webhook", {"event_types": ["Bad Event"]}, {"signing_secret": {"env": "S"}}, "event_types"),
                ("carrier-pigeon", {}, {}, "unknown integration kind")):
            with pytest.raises(service.IntegrationError, match=why):
                service.create(conn, ws, kind, "n", config, secrets, "user:a")


def test_testing_a_connection_reports_ok_or_the_failure_class_and_records_it(world, ws, net):
    integration = connect_slack(ws)
    with get_db() as conn:
        ok = service.test_connection(conn, ws, integration, "user:admin")
        assert ok["ok"] and "patchquest-bot" in ok["detail"]
        assert service.get(conn, ws, integration)["status"] == "connected"
    net.slack._token = "rotated-elsewhere"  # the token stops working
    with get_db() as conn:
        bad = service.test_connection(conn, ws, integration, "user:admin")
        assert bad["ok"] is False and "CONNECTOR_AUTH" in bad["error"]
        row = service.get(conn, ws, integration)
        assert row["status"] == "error" and row["last_error"] and "xoxb-token" not in str(row)


def test_deleting_an_integration_deletes_its_secrets(world, ws, net):
    integration = connect_github(ws)
    with get_db() as conn:
        service.delete(conn, ws, integration, "user:admin")
        assert conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM integrations").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_rejected_deliveries_are_audited_only_up_to_a_cap_per_minute(world, ws, net, monkeypatch):
    from patchquest.api import routes_hooks
    monkeypatch.setattr(routes_hooks, "_LIMIT", 10_000)
    routes_hooks._hits.clear()
    routes_hooks._rejected.clear()
    integration = connect_github(ws)
    async with world.client() as c:
        for i in range(routes_hooks._AUDIT_PER_WINDOW + 30):
            assert (await post_hook(c, integration, github_delivery(b"wrong", "issues", {"action": "labeled"}, f"d{i}"))).status_code == 401
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'webhook.rejected'").fetchone()[0] == routes_hooks._AUDIT_PER_WINDOW

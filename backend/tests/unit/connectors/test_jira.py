"""MOCKED_PROTOCOL_TEST: runs against MockJira; nothing here is verified against live Jira."""

from __future__ import annotations

from datetime import timedelta

import pytest

from patchquest.connectors.base import ApprovalGrant, ApprovalRequired, MalformedEvent, SecretRef, UnsupportedEvent
from patchquest.connectors.envelope import SignatureStatus
from patchquest.connectors.jira import Comment, JiraConnector
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import Fault, MockJira, jira_delivery
from patchquest.domain.failures import FailureKind, Origin, PatchQuestError, classify
from tests.unit.connectors.conftest import SECRET_VALUE, FakeClock, resolver

pytestmark = pytest.mark.mocked_protocol
ENV = {"J_EMAIL": "bot@example.test", "J_TOKEN": SECRET_VALUE, "J_WH": "jira-secret"}
SITE = "https://acme.atlassian.net"


def make(server=None, clock=None):
    server = server or MockJira("bot@example.test", SECRET_VALUE, "OPS", [1, 2])
    clock = clock or FakeClock()
    conn = JiraConnector(workspace_id="ws", base_url=SITE, project_key="OPS", email_ref=SecretRef("J_EMAIL"), api_token_ref=SecretRef("J_TOKEN"),
                         webhook_secret_ref=SecretRef("J_WH"), http=SafeHttp(transport=server.transport(), resolve=resolver()), clock=clock,
                         environ=ENV)
    return server, clock, conn


def grant(clock, key):
    return ApprovalGrant("comment", key, "alice", clock() + timedelta(minutes=5))


def event(kind="jira:issue_created", project="OPS"):
    data = {"webhookEvent": kind, "timestamp": 1760000000000, "user": {"displayName": "Ana"},
            "issue": {"key": "OPS-1", "fields": {"summary": "Disk full", "description": "df says 100%", "labels": ["agent-ready"],
                                                  "status": {"name": "Open"}, "project": {"key": project}}}}
    if kind == "comment_created":
        data["comment"] = {"body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "any update?"}]}]}}
    return data


def test_events_become_envelopes():
    _, _, conn = make()
    for kind, expected in (("jira:issue_created", "issue_created"), ("jira:issue_updated", "issue_updated"), ("comment_created", "comment_created")):
        d = jira_delivery(b"jira-secret", event(kind), "wh-" + expected)
        assert conn.verify(d.headers, d.body) is SignatureStatus.VERIFIED
        env = conn.normalize(d.body, d.headers)
        assert (env.type, env.external_id, env.actor) == (expected, "wh-" + expected, "Ana")
        assert env.payload["key"] == "OPS-1" and env.payload["url"] == f"{SITE}/browse/OPS-1"
    assert conn.normalize(*[getattr(jira_delivery(b"jira-secret", event("comment_created"), "c"), a) for a in ("body", "headers")]).payload["comment"] == "any update?"


def test_other_projects_and_events_are_not_triggers_and_bad_bodies_are_malformed():
    _, _, conn = make()
    for body in (event(project="HR"), event("jira:issue_deleted")):
        d = jira_delivery(b"jira-secret", body, "x")
        with pytest.raises(UnsupportedEvent):
            conn.normalize(d.body, d.headers)
    for raw in (b"{", b"[]", b'{"webhookEvent":"jira:issue_created"}'):
        with pytest.raises(MalformedEvent):
            conn.normalize(raw, {"X-Atlassian-Webhook-Identifier": "d"})
    with pytest.raises(MalformedEvent):
        conn.normalize(b"{}", {})


def test_signature_checks():
    _, _, conn = make()
    d = jira_delivery(b"nope", event(), "d")
    assert conn.verify(d.headers, d.body) is SignatureStatus.INVALID
    assert conn.verify({"X-Hub-Signature": "md5=abc"}, b"{}") is SignatureStatus.INVALID
    assert conn.verify({}, b"{}") is SignatureStatus.UNVERIFIED


def test_comment_requires_a_grant_posts_adf_with_a_marker_and_reconciles():
    server, clock, conn = make()
    with pytest.raises(ApprovalRequired):
        conn.perform(Comment(issue_key="OPS-1", body="done"), idempotency_key="k1", grant=None)
    assert server.requests == []
    first = conn.perform(Comment(issue_key="OPS-1", body="done\nsecond line"), idempotency_key="k1", grant=grant(clock, "k1"))
    assert first.created and "patchquest:idempotency:k1" in server.all_text()[0]
    again = conn.perform(Comment(issue_key="OPS-1", body="done"), idempotency_key="k1", grant=grant(clock, "k1"))
    assert again.created is False and len(server.comments["OPS-1"]) == 1


def test_a_lost_response_does_not_duplicate_the_comment():
    server, clock, conn = make()
    server.script.push(Fault(status=502, after_effect=True))
    with pytest.raises(Exception):  # noqa: B017
        conn.perform(Comment(issue_key="OPS-2", body="x"), idempotency_key="k2", grant=grant(clock, "k2"))
    conn.perform(Comment(issue_key="OPS-2", body="x"), idempotency_key="k2", grant=grant(clock, "k2"))
    assert len(server.comments["OPS-2"]) == 1


def test_other_projects_are_refused_before_any_request_and_bad_credentials_map_to_auth():
    server, clock, conn = make()
    with pytest.raises(PatchQuestError) as exc:
        conn.perform(Comment(issue_key="HR-9", body="x"), idempotency_key="k3", grant=grant(clock, "k3"))
    assert exc.value.kind is FailureKind.POLICY_DENIED and server.requests == []
    _, c2, conn2 = make(server=MockJira("bot@example.test", "wrong", "OPS"))
    with pytest.raises(Exception) as auth:
        conn2.perform(Comment(issue_key="OPS-1", body="x"), idempotency_key="k4", grant=grant(c2, "k4"))
    assert classify(auth.value, origin=Origin.CONNECTOR).kind is FailureKind.CONNECTOR_AUTH


def test_secrets_are_scrubbed_from_outgoing_text_and_never_appear_in_repr():
    server, clock, conn = make()
    conn.perform(Comment(issue_key="OPS-1", body="sk-ant-api03-" + "q" * 40), idempotency_key="k5", grant=grant(clock, "k5"))
    assert "sk-ant" not in server.all_text()[0]
    assert SECRET_VALUE not in repr(conn)
    with pytest.raises(Exception):  # noqa: B017
        Comment(issue_key="not a key", body="x")


def test_construction_refuses_a_plain_http_site_and_a_bad_project_key():
    with pytest.raises(ValueError, match="https"):
        JiraConnector(workspace_id="w", base_url="http://x", project_key="OPS", email_ref=SecretRef("a"), api_token_ref=SecretRef("b"), webhook_secret_ref=SecretRef("c"))
    with pytest.raises(ValueError, match="PROJ"):
        JiraConnector(workspace_id="w", base_url=SITE, project_key="ops", email_ref=SecretRef("a"), api_token_ref=SecretRef("b"), webhook_secret_ref=SecretRef("c"))

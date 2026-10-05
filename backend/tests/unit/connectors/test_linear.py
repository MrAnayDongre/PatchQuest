"""MOCKED_PROTOCOL_TEST: runs against MockLinear; nothing here is verified against live Linear."""

from __future__ import annotations

from datetime import timedelta

import pytest

from patchquest.connectors.base import ApprovalGrant, ApprovalRequired, MalformedEvent, SecretRef, UnsupportedEvent
from patchquest.connectors.envelope import SignatureStatus
from patchquest.connectors.linear import Comment, LinearConnector
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import Fault, MockLinear, linear_delivery
from patchquest.domain.failures import FailureKind, PatchQuestError
from tests.unit.connectors.conftest import SECRET_VALUE, FakeClock, resolver

pytestmark = pytest.mark.mocked_protocol
ENV = {"LIN_KEY": SECRET_VALUE, "LIN_WH": "lin-secret"}


def make(server=None, clock=None, **kw):
    server = server or MockLinear(SECRET_VALUE, ["iss-1", "iss-2"])
    clock = clock or FakeClock()
    conn = LinearConnector(workspace_id="ws", api_key_ref=SecretRef("LIN_KEY"), webhook_secret_ref=SecretRef("LIN_WH"),
                           http=SafeHttp(transport=server.transport(), resolve=resolver()), clock=clock, environ=ENV, **kw)
    return server, clock, conn


def grant(clock, key):
    return ApprovalGrant("comment", key, "alice", clock() + timedelta(minutes=5))


def payload(clock, type_="Issue", action="create", team="ENG", **data):
    base = {"id": "iss-1", "identifier": "ENG-7", "title": "Crash on save", "description": "stack trace...", "team": {"key": team},
            "labels": [{"name": "agent-ready"}], "state": {"name": "Todo"}}
    if type_ == "Comment":
        base = {"id": "cm-1", "issueId": "iss-1", "body": "looks like a regression", "team": {"key": team}}
    return {"action": action, "type": type_, "data": {**base, **data}, "url": "https://linear.app/x/issue/ENG-7",
            "actor": {"name": "Ana"}, "webhookTimestamp": int(clock().timestamp() * 1000)}


def test_issue_and_comment_events_become_envelopes():
    _, clock, conn = make(team_key="ENG")
    d = linear_delivery(b"lin-secret", payload(clock), "dl-1")
    assert conn.verify(d.headers, d.body) is SignatureStatus.VERIFIED
    env = conn.normalize(d.body, d.headers)
    assert (env.type, env.external_id, env.actor) == ("Issue.create", "dl-1", "Ana") and env.payload["labels"] == ["agent-ready"]
    c = linear_delivery(b"lin-secret", payload(clock, "Comment"), "dl-2")
    assert conn.normalize(c.body, c.headers).payload["issue_id"] == "iss-1"


def test_other_teams_and_other_event_types_are_not_triggers():
    _, clock, conn = make(team_key="ENG")
    for body in (payload(clock, team="OPS"), payload(clock, action="remove"), payload(clock, type_="Project")):
        d = linear_delivery(b"lin-secret", body, "x")
        with pytest.raises(UnsupportedEvent):
            conn.normalize(d.body, d.headers)


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"type":"Issue","action":"create"}'])
def test_malformed_bodies_are_rejected(raw):
    _, _, conn = make()
    with pytest.raises(MalformedEvent):
        conn.normalize(raw, {"Linear-Delivery": "d"})
    with pytest.raises(MalformedEvent):
        conn.normalize(raw, {})


def test_signature_must_match_and_the_delivery_must_be_recent():
    _, clock, conn = make()
    good = linear_delivery(b"lin-secret", payload(clock), "d")
    assert conn.verify(good.headers, good.body) is SignatureStatus.VERIFIED
    forged = linear_delivery(b"other", payload(clock), "d")
    assert conn.verify(forged.headers, forged.body) is SignatureStatus.INVALID
    clock.advance(120)  # the delivery is now two minutes old: outside Linear's 60 second window
    assert conn.verify(good.headers, good.body) is SignatureStatus.INVALID
    assert conn.verify({}, b"{}") is SignatureStatus.UNVERIFIED


def test_comment_needs_a_grant_then_posts_with_a_marker_and_reconciles():
    server, clock, conn = make()
    with pytest.raises(ApprovalRequired):
        conn.perform(Comment(issue_id="iss-1", body="fixed"), idempotency_key="k1", grant=None)
    assert server.requests == []
    first = conn.perform(Comment(issue_id="iss-1", body="fixed"), idempotency_key="k1", grant=grant(clock, "k1"))
    assert first.created and "patchquest:idempotency:k1" in server.comments["iss-1"][0]["body"]
    again = conn.perform(Comment(issue_id="iss-1", body="fixed"), idempotency_key="k1", grant=grant(clock, "k1"))
    assert again.created is False and len(server.comments["iss-1"]) == 1


def test_a_response_lost_after_the_effect_does_not_duplicate_the_comment():
    server, clock, conn = make()
    server.script.push(Fault(status=500, after_effect=True))
    with pytest.raises(Exception):  # noqa: B017
        conn.perform(Comment(issue_id="iss-2", body="x"), idempotency_key="k2", grant=grant(clock, "k2"))
    conn.perform(Comment(issue_id="iss-2", body="x"), idempotency_key="k2", grant=grant(clock, "k2"))
    assert len(server.comments["iss-2"]) == 1


def test_graphql_errors_map_to_failure_kinds_and_secrets_in_text_are_scrubbed():
    server, clock, conn = make()
    conn.perform(Comment(issue_id="iss-1", body="token sk-ant-api03-" + "y" * 40), idempotency_key="k3", grant=grant(clock, "k3"))
    assert "sk-ant" not in server.comments["iss-1"][0]["body"]
    with pytest.raises(PatchQuestError) as missing:
        conn.perform(Comment(issue_id="nope", body="x"), idempotency_key="k4", grant=grant(clock, "k4"))
    assert missing.value.kind is FailureKind.TOOL_FAILURE
    _, c2, conn2 = make(server=MockLinear("different", ["iss-1"]))
    with pytest.raises(PatchQuestError) as auth:
        conn2.perform(Comment(issue_id="iss-1", body="x"), idempotency_key="k5", grant=grant(c2, "k5"))
    assert auth.value.kind is FailureKind.CONNECTOR_AUTH


def test_only_comment_exists_and_credentials_do_not_leak_through_repr():
    _, _, conn = make()
    assert [a.name for a in conn.spec.actions] == ["comment"]  # no state changes, reassignment or deletion
    assert SECRET_VALUE not in repr(conn)
    with pytest.raises(Exception):  # noqa: B017
        Comment(issue_id="has space", body="x")

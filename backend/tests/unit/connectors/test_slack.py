"""MOCKED_PROTOCOL_TEST: runs against MockSlack; nothing here is verified against live Slack."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest

from patchquest.connectors.base import ApprovalGrant, ApprovalRequired, MalformedEvent, SecretRef, UnsupportedEvent
from patchquest.connectors.envelope import SignatureStatus
from patchquest.connectors.slack import PostMessage, SlackConnector
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import (
    EXPIRED_CREDENTIAL,
    PASS,
    Fault,
    MockSlack,
    rate_limited,
    slack_delivery,
)
from patchquest.domain.failures import FailureKind, Origin, PatchQuestError, classify
from tests.unit.connectors.conftest import SECRET_VALUE, FakeClock, resolver

pytestmark = pytest.mark.mocked_protocol
ENV = {"SLACK_TOKEN": SECRET_VALUE, "SLACK_SIGNING": "signing-secret"}
CHANNEL = "C0123ABC"


def make(server=None, clock=None, **kw):
    server = server or MockSlack(SECRET_VALUE, [CHANNEL, "C9999ZZZ"])
    clock = clock or FakeClock()
    conn = SlackConnector(workspace_id="ws", bot_token_ref=SecretRef("SLACK_TOKEN"), signing_secret_ref=SecretRef("SLACK_SIGNING"),
                          allowed_channels=kw.pop("allowed_channels", (CHANNEL,)), http=SafeHttp(transport=server.transport(), resolve=resolver()),
                          clock=clock, environ=ENV, **kw)
    return server, clock, conn


def grant(clock, key):
    return ApprovalGrant("post_message", key, "alice", clock() + timedelta(minutes=5))


def event(kind="app_mention", **over):
    inner = {"type": kind, "user": "U1", "text": "<@UBOT> please fix the build", "ts": "1760000000.000100", "channel": CHANNEL, **over}
    if kind == "message":
        inner.setdefault("channel_type", "channel")
    return {"type": "event_callback", "team_id": "T1", "event_id": "Ev1", "event_time": 1760000000, "event": inner}


def delivery(conn_clock, payload, secret=b"signing-secret"):
    return slack_delivery(secret, payload, int(conn_clock().timestamp()))


def test_a_mention_becomes_an_envelope_and_the_signature_verifies():
    _, clock, conn = make()
    d = delivery(clock, event())
    assert conn.verify(d.headers, d.body) is SignatureStatus.VERIFIED
    env = conn.normalize(d.body, d.headers)
    assert (env.source, env.type, env.external_id, env.actor, env.workspace_id) == ("slack", "app_mention", "Ev1", "U1", "ws")
    assert env.payload["channel"] == CHANNEL and "fix the build" in env.payload["text"]


def test_channel_messages_are_triggers_but_dms_and_other_channels_are_not():
    _, clock, conn = make()
    assert conn.normalize(*[getattr(delivery(clock, event("message")), a) for a in ("body", "headers")]).type == "message.channels"
    for payload in (event("message", channel_type="im"), event("app_mention", channel="C5555XXX"), event("reaction_added")):
        d = delivery(clock, payload)
        with pytest.raises(UnsupportedEvent):
            conn.normalize(d.body, d.headers)


def test_bot_and_system_messages_never_start_anything_so_the_app_cannot_trigger_itself():
    _, clock, conn = make()
    for extra in ({"bot_id": "B1"}, {"subtype": "bot_message"}, {"subtype": "message_changed"}):
        d = delivery(clock, event("message", **extra))
        with pytest.raises(UnsupportedEvent, match="bot"):
            conn.normalize(d.body, d.headers)


def test_another_slack_workspace_is_refused_when_one_is_pinned():
    _, clock, conn = make(team_id="T-OTHER")
    d = delivery(clock, event())
    with pytest.raises(UnsupportedEvent, match="different Slack"):
        conn.normalize(d.body, d.headers)


@pytest.mark.parametrize("bad", [b"{", b"[]", b'{"type":"event_callback"}', b'{"type":"event_callback","event":{"type":"app_mention"}}'])
def test_malformed_bodies_are_rejected(bad):
    _, _, conn = make()
    with pytest.raises((MalformedEvent, UnsupportedEvent)):
        conn.normalize(bad, {})


def test_signatures_must_be_fresh_and_correct():
    _, clock, conn = make()
    wrong = delivery(clock, event(), secret=b"another")
    assert conn.verify(wrong.headers, wrong.body) is SignatureStatus.INVALID
    old = slack_delivery(b"signing-secret", event(), int(clock().timestamp()) - 400)
    assert conn.verify(old.headers, old.body) is SignatureStatus.INVALID  # replayed
    assert conn.verify({}, b"{}") is SignatureStatus.UNVERIFIED


def test_the_url_verification_handshake_is_answered_with_the_challenge_only():
    _, clock, conn = make()
    d = delivery(clock, {"type": "url_verification", "challenge": "abc123"})
    assert conn.handshake(d.headers, d.body) == {"challenge": "abc123"}
    assert conn.handshake(*[getattr(delivery(clock, event()), a) for a in ("headers", "body")]) is None
    assert conn.handshake({}, b"not json") is None


def test_posting_needs_a_grant_and_sends_nothing_without_one():
    server, _, conn = make()
    with pytest.raises(ApprovalRequired):
        conn.perform(PostMessage(channel=CHANNEL, text="hi"), idempotency_key="k1", grant=None)
    assert server.requests == []


def test_post_message_happy_path_with_metadata_for_reconciliation():
    server, clock, conn = make()
    result = conn.perform(PostMessage(channel=CHANNEL, text="PR opened: https://example.test/1"), idempotency_key="k1", grant=grant(clock, "k1"))
    assert result.created and result.external_id.startswith(CHANNEL + ":")
    [message] = server.messages[CHANNEL]
    assert message["metadata"]["event_payload"]["idempotency_key"] == "k1"


def test_a_crash_after_the_post_does_not_post_twice_on_retry():
    server, clock, conn = make()
    server.script.push(PASS, Fault(status=500, after_effect=True))  # the first request is the reconciliation lookup
    with pytest.raises(Exception):  # noqa: B017 - the response was lost
        conn.perform(PostMessage(channel=CHANNEL, text="once"), idempotency_key="k9", grant=grant(clock, "k9"))
    assert len(server.messages[CHANNEL]) == 1
    again = conn.perform(PostMessage(channel=CHANNEL, text="once"), idempotency_key="k9", grant=grant(clock, "k9"))
    assert again.created is False and len(server.messages[CHANNEL]) == 1


def test_only_allowlisted_channels_can_be_posted_to_and_nothing_is_sent_otherwise():
    server, clock, conn = make()
    with pytest.raises(PatchQuestError) as exc:
        conn.perform(PostMessage(channel="C9999ZZZ", text="leak"), idempotency_key="k2", grant=grant(clock, "k2"))
    assert exc.value.kind is FailureKind.POLICY_DENIED
    assert [r for r in server.requests if r.url.path.endswith("postMessage")] == []


def test_secrets_in_outgoing_text_are_scrubbed():
    server, clock, conn = make()
    conn.perform(PostMessage(channel=CHANNEL, text="key is sk-ant-api03-" + "z" * 40), idempotency_key="k3", grant=grant(clock, "k3"))
    assert "sk-ant" not in server.texts()[0]


def test_errors_are_mapped_to_failure_kinds():
    server, clock, conn = make()
    server.script.push(EXPIRED_CREDENTIAL)
    with pytest.raises(Exception) as exc:
        conn.perform(PostMessage(channel=CHANNEL, text="x"), idempotency_key="k4", grant=grant(clock, "k4"))
    assert classify(exc.value, origin=Origin.CONNECTOR).kind is FailureKind.CONNECTOR_AUTH
    server.script.push(rate_limited("1"))
    with pytest.raises(Exception) as limited:
        conn.perform(PostMessage(channel=CHANNEL, text="x"), idempotency_key="k5", grant=grant(clock, "k5"))
    failure = classify(limited.value, origin=Origin.CONNECTOR)
    assert failure.kind is FailureKind.CONNECTOR_RATE_LIMIT and failure.retry_after == 1
    bad = MockSlack("other-token", [CHANNEL])
    _, c2, conn2 = make(server=bad)
    with pytest.raises(PatchQuestError) as auth:
        conn2.perform(PostMessage(channel=CHANNEL, text="x"), idempotency_key="k6", grant=grant(c2, "k6"))
    assert auth.value.kind is FailureKind.CONNECTOR_AUTH  # Slack says ok:false with HTTP 200


def test_action_validation_and_credentials_never_leak(caplog):
    for bad in (dict(channel="general", text="x"), dict(channel=CHANNEL, text=""), dict(channel=CHANNEL, text="x", thread_ts="yesterday"),
                dict(channel=CHANNEL, text="x" * 3001)):
        with pytest.raises(Exception):  # noqa: B017
            PostMessage(**bad)
    _, clock, conn = make()
    with caplog.at_level(logging.DEBUG):
        conn.perform(PostMessage(channel=CHANNEL, text="ok"), idempotency_key="k7", grant=grant(clock, "k7"))
    assert SECRET_VALUE not in repr(conn) and SECRET_VALUE not in caplog.text and "signing-secret" not in repr(conn)
    assert json.dumps(conn.spec.triggers)  # triggers declared

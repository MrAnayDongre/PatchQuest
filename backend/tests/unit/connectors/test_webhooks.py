from __future__ import annotations

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from patchquest.connectors import webhooks
from patchquest.connectors.base import SecretRef
from patchquest.connectors.envelope import SignatureStatus
from patchquest.connectors.github import GitHubConnector
from patchquest.connectors.testing.mock_server import duplicated, github_delivery, out_of_order, slack_delivery
from patchquest.connectors.webhooks import (
    Accepted,
    Duplicate,
    Rejected,
    WebhookReceiver,
    verify_github_signature,
    verify_slack_signature,
)
from patchquest.database import get_db
from tests.unit.connectors.conftest import FakeClock

SECRET = b"whsec-test"
REPO = "acme/widgets"


def _issue_event(number: int = 1) -> dict:
    return {"action": "opened", "repository": {"full_name": REPO}, "sender": {"login": "octo"},
            "issue": {"number": number, "title": "t", "body": "b", "labels": [], "html_url": "u",
                      "updated_at": "2026-01-01T00:00:00Z"}}


@pytest.fixture
def connector():
    return GitHubConnector(REPO, token_ref=SecretRef("T"), webhook_secret_ref=SecretRef("S"), workspace_id="ws",
                           environ={"S": SECRET.decode(), "T": "tok"})


def _count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM connector_events").fetchone()[0]


# --- signatures -------------------------------------------------------------------------------
def test_github_signature_valid_invalid_tampered_wrong_secret():
    d = github_delivery(SECRET, "issues", _issue_event(), "d1")
    assert verify_github_signature(SECRET, d.headers, d.body) is SignatureStatus.VERIFIED
    assert verify_github_signature(SECRET, d.headers, d.body + b" ") is SignatureStatus.INVALID
    assert verify_github_signature(b"other", d.headers, d.body) is SignatureStatus.INVALID
    assert verify_github_signature(SECRET, {}, d.body) is SignatureStatus.UNVERIFIED
    bad = {**d.headers, "X-Hub-Signature-256": "sha1=abc"}
    assert verify_github_signature(SECRET, bad, d.body) is SignatureStatus.INVALID
    odd = {**d.headers, "X-Hub-Signature-256": "sha256=éé"}
    assert verify_github_signature(SECRET, odd, d.body) is SignatureStatus.INVALID
    lower = {k.lower(): v for k, v in d.headers.items()}
    assert verify_github_signature(SECRET, lower, d.body) is SignatureStatus.VERIFIED


def test_comparison_is_constant_time_primitive():
    d = github_delivery(SECRET, "issues", _issue_event(), "d1")
    with mock.patch.object(webhooks.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
        verify_github_signature(SECRET, d.headers, d.body)
    assert spy.call_count == 1


def test_empty_secret_refused():
    with pytest.raises(ValueError, match="empty"):
        verify_github_signature(b"", {"X-Hub-Signature-256": "sha256=00"}, b"x")


def test_slack_signature_and_replay_window_both_sides():
    now = 1_700_000_000
    ok = slack_delivery(SECRET, {"a": 1}, now)
    assert verify_slack_signature(SECRET, ok.headers, ok.body, now=now) is SignatureStatus.VERIFIED
    assert verify_slack_signature(SECRET, ok.headers, ok.body, now=now + 300) is SignatureStatus.VERIFIED
    assert verify_slack_signature(SECRET, ok.headers, ok.body, now=now + 301) is SignatureStatus.INVALID  # too old
    assert verify_slack_signature(SECRET, ok.headers, ok.body, now=now - 301) is SignatureStatus.INVALID  # from the future
    assert verify_slack_signature(SECRET, ok.headers, ok.body + b"x", now=now) is SignatureStatus.INVALID
    assert verify_slack_signature(b"x", ok.headers, ok.body, now=now) is SignatureStatus.INVALID
    assert verify_slack_signature(SECRET, {}, ok.body, now=now) is SignatureStatus.UNVERIFIED
    swapped = {**ok.headers, "X-Slack-Request-Timestamp": str(now + 1)}  # timestamp is part of the signed text
    assert verify_slack_signature(SECRET, swapped, ok.body, now=now) is SignatureStatus.INVALID
    junk = {**ok.headers, "X-Slack-Request-Timestamp": "abc"}
    assert verify_slack_signature(SECRET, junk, ok.body, now=now) is SignatureStatus.INVALID


def test_slack_matches_documented_base_string():
    now, body = 1_700_000_000, b'{"a":1}'
    sig = "v0=" + hmac.new(SECRET, f"v0:{now}:".encode() + body, hashlib.sha256).hexdigest()
    headers = {"x-slack-request-timestamp": str(now), "x-slack-signature": sig}
    assert verify_slack_signature(SECRET, headers, body, now=now) is SignatureStatus.VERIFIED


# --- receiving, dedup -------------------------------------------------------------------------
def test_accept_then_duplicate_returns_original_outcome(connector):
    rx = WebhookReceiver(clock=FakeClock())
    d = github_delivery(SECRET, "issues", _issue_event(), "del-1")
    first = rx.receive(connector, d.headers, d.body)
    assert isinstance(first, Accepted)
    assert first.envelope.external_id == "del-1"
    rx.record_outcome("ws", "github", "del-1", "STARTED", run_id="run-9")
    again = rx.receive(connector, d.headers, d.body)
    assert again == Duplicate("github", "del-1", "STARTED", "run-9")
    assert _count() == 1


def test_duplicate_injection_and_reordering(connector):
    rx = WebhookReceiver()
    deliveries = [github_delivery(SECRET, "issues", _issue_event(n), f"d{n}") for n in (1, 2, 3)]
    stream = [x for d in out_of_order(deliveries) for x in duplicated(d)]
    results = [rx.receive(connector, d.headers, d.body) for d in stream]
    assert sum(isinstance(r, Accepted) for r in results) == 3
    assert sum(isinstance(r, Duplicate) for r in results) == 3
    assert [d.headers["X-GitHub-Delivery"] for d in out_of_order(deliveries)] != ["d1", "d2", "d3"]


def test_concurrent_double_delivery_stores_once(connector):
    rx = WebhookReceiver()
    d = github_delivery(SECRET, "issues", _issue_event(), "race")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: rx.receive(connector, d.headers, d.body), range(16)))
    assert sum(isinstance(r, Accepted) for r in results) == 1
    assert sum(isinstance(r, Duplicate) for r in results) == 15
    assert _count() == 1


def test_invalid_signature_stores_nothing(connector):
    rx = WebhookReceiver()
    d = github_delivery(b"wrong", "issues", _issue_event(), "x")
    assert rx.receive(connector, d.headers, d.body) == Rejected("invalid_signature")
    unsigned = {k: v for k, v in d.headers.items() if k != "X-Hub-Signature-256"}
    assert rx.receive(connector, unsigned, d.body) == Rejected("unverified_signature")
    assert _count() == 0


def test_body_size_limit_checked_before_verification(connector):
    rx = WebhookReceiver(max_body_bytes=100)
    d = github_delivery(SECRET, "issues", {"pad": "x" * 500}, "big")
    with mock.patch.object(connector, "verify") as verify:
        assert rx.receive(connector, d.headers, d.body) == Rejected("body_too_large")
    verify.assert_not_called()
    assert _count() == 0


def test_signed_but_unsupported_or_malformed_not_stored(connector):
    rx = WebhookReceiver()
    ping = github_delivery(SECRET, "ping", {"zen": "x"}, "p1")
    assert rx.receive(connector, ping.headers, ping.body) == Rejected("unsupported_event")
    other_repo = _issue_event()
    other_repo["repository"]["full_name"] = "evil/other"
    d = github_delivery(SECRET, "issues", other_repo, "p2")
    assert rx.receive(connector, d.headers, d.body) == Rejected("unsupported_event")
    broken = github_delivery(SECRET, "issues", {"action": "opened", "repository": {"full_name": REPO}}, "p3")
    assert rx.receive(connector, broken.headers, broken.body) == Rejected("malformed")
    assert _count() == 0


def test_record_outcome_validation():
    rx = WebhookReceiver()
    with pytest.raises(ValueError, match="unknown event status"):
        rx.record_outcome("ws", "github", "x", "BOGUS")
    with pytest.raises(KeyError):
        rx.record_outcome("ws", "github", "missing", "STARTED")


def test_the_same_external_id_in_two_workspaces_is_two_events_not_a_collision():
    """Dedup is per workspace: one tenant sending an id first must not make another tenant's delivery a 'duplicate'."""
    from patchquest.connectors.webhooks import Accepted, Duplicate, WebhookReceiver

    def conn_for(ws):
        return GitHubConnector(REPO, token_ref=SecretRef("T"), webhook_secret_ref=SecretRef("S"), workspace_id=ws,
                               environ={"S": SECRET.decode(), "T": "tok"})

    d = github_delivery(SECRET, "issues", _issue_event(), "same-delivery-id")
    rx = WebhookReceiver()
    assert isinstance(rx.receive(conn_for("ws-a"), d.headers, d.body), Accepted)
    assert isinstance(rx.receive(conn_for("ws-b"), d.headers, d.body), Accepted)  # another tenant: independent
    assert isinstance(rx.receive(conn_for("ws-a"), d.headers, d.body), Duplicate)  # the same tenant: a real duplicate
    assert _count() == 2

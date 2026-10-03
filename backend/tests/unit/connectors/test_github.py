"""MOCKED_PROTOCOL_TEST: every test here runs against MockGitHub; nothing is verified against live GitHub."""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from patchquest.connectors.base import ApprovalGrant, ApprovalRequired, MalformedEvent, SecretRef, UnsupportedEvent
from patchquest.connectors.envelope import SignatureStatus
from patchquest.connectors.github import AddLabel, Comment, CreatePullRequest, GitHubConnector, marker
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import (
    EXPIRED_CREDENTIAL,
    PASS,
    TIMEOUT,
    Fault,
    MockGitHub,
    github_delivery,
    rate_limited,
)
from patchquest.domain.failures import FailureKind, PatchQuestError
from tests.unit.connectors.conftest import SECRET_VALUE, FakeClock, resolver

pytestmark = pytest.mark.mocked_protocol
REPO = "acme/widgets"
ENV = {"GH_TOKEN": SECRET_VALUE, "GH_WH": "whsec"}


def _make(server: MockGitHub | None = None, clock: FakeClock | None = None):
    server = server or MockGitHub(REPO, SECRET_VALUE)
    clock = clock or FakeClock()
    http = SafeHttp(transport=server.transport(), resolve=resolver())
    conn = GitHubConnector(REPO, token_ref=SecretRef("GH_TOKEN"), webhook_secret_ref=SecretRef("GH_WH"),
                           workspace_id="ws", http=http, clock=clock, environ=ENV)
    return server, clock, conn


def _grant(clock, action, key):
    return ApprovalGrant(action, key, "alice", clock() + timedelta(minutes=5))


def test_normalize_triggers():
    _, _, conn = _make()
    cases = {
        ("issues", "opened"): {"issue": {"number": 3, "title": "T", "body": None, "labels": [{"name": "bug"}]}},
        ("issues", "labeled"): {"issue": {"number": 3, "title": "T", "labels": []}, "label": {"name": "bug"}},
        ("pull_request", "opened"): {"pull_request": {"number": 4, "title": "P", "head": {"sha": "abc"}, "base": {"ref": "main"}}},
        ("check_suite", "completed"): {"check_suite": {"conclusion": "success", "head_sha": "abc", "head_branch": "x"}},
    }
    for (event, action), extra in cases.items():
        payload = {"action": action, "repository": {"full_name": REPO}, "sender": {"login": "octo"}, **extra}
        d = github_delivery(b"whsec", event, payload, f"id-{event}")
        env = conn.normalize(d.body, d.headers)
        assert (env.type, env.external_id, env.actor, env.workspace_id) == (f"{event}.{action}", f"id-{event}", "octo", "ws")
        assert env.payload["repository"] == REPO
        assert conn.verify(d.headers, d.body) is SignatureStatus.VERIFIED


def test_normalize_rejections():
    _, _, conn = _make()
    good = {"action": "opened", "repository": {"full_name": REPO}, "sender": {"login": "o"}}
    hdr = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d"}
    with pytest.raises(MalformedEvent):
        conn.normalize(b"{", hdr)
    with pytest.raises(MalformedEvent):
        conn.normalize(b"[]", hdr)
    with pytest.raises(MalformedEvent):
        conn.normalize(b"{}", {})
    with pytest.raises(UnsupportedEvent):
        conn.normalize(b'{"action":"closed"}', hdr)
    with pytest.raises(MalformedEvent):
        conn.normalize(b'{"action":"opened","repository":{"full_name":"acme/widgets"}}', hdr)  # no sender/issue
    import json
    with pytest.raises(UnsupportedEvent):
        conn.normalize(json.dumps({**good, "repository": {"full_name": "x/y"}}).encode(), hdr)


def test_write_needs_grant_and_no_request_is_made_without_one():
    server, clock, conn = _make()
    n = server.add_issue()
    with pytest.raises(ApprovalRequired):
        conn.perform(Comment(issue_number=n, body="hi"), idempotency_key="k1", grant=None)
    assert server.requests == []


def test_comment_label_pr_happy_path():
    server, clock, conn = _make()
    n = server.add_issue()
    r = conn.perform(Comment(issue_number=n, body="hi"), idempotency_key="k1", grant=_grant(clock, "comment", "k1"))
    assert r.created and marker("k1") in server.all_comment_bodies()[0]
    conn.perform(AddLabel(issue_number=n, label="bug"), idempotency_key="k2", grant=_grant(clock, "add_label", "k2"))
    conn.perform(AddLabel(issue_number=n, label="bug"), idempotency_key="k3", grant=_grant(clock, "add_label", "k3"))
    assert server.issues[n]["labels"] == ["bug"]
    pr = conn.perform(CreatePullRequest(title="Fix", head="fix/x", base="main"), idempotency_key="k4",
                      grant=_grant(clock, "create_pull_request", "k4"))
    assert len(server.pulls()) == 1 and pr.url and marker("k4") in server.pulls()[0]["body"]


def test_comment_idempotent_across_simulated_crash():
    server, clock, conn = _make()
    n = server.add_issue()
    server.script.push(PASS, Fault(timeout=True, after_effect=True))  # PASS: the reconciliation search  # created, but the response was lost
    action, grant = Comment(issue_number=n, body="hi"), _grant(clock, "comment", "crash-1")
    with pytest.raises(httpx.TimeoutException):
        conn.perform(action, idempotency_key="crash-1", grant=grant)
    assert len(server.all_comment_bodies()) == 1
    again = conn.perform(action, idempotency_key="crash-1", grant=grant)
    assert again.created is False and len(server.all_comment_bodies()) == 1


def test_pull_request_idempotent_across_simulated_crash():
    server, clock, conn = _make()
    action = CreatePullRequest(title="Fix", head="fix/x", base="main")
    grant = _grant(clock, "create_pull_request", "pr-1")
    server.script.push(PASS, Fault(timeout=True, after_effect=True))  # PASS: the reconciliation search
    with pytest.raises(httpx.TimeoutException):
        conn.perform(action, idempotency_key="pr-1", grant=grant)
    result = conn.perform(action, idempotency_key="pr-1", grant=grant)
    assert result.created is False and len(server.pulls()) == 1
    other = conn.perform(action, idempotency_key="pr-2", grant=_grant(clock, "create_pull_request", "pr-2"))
    assert other.created and len(server.pulls()) == 2


def test_marker_for_prefix_key_is_not_confused():
    server, clock, conn = _make()
    n = server.add_issue()
    conn.perform(Comment(issue_number=n, body="a"), idempotency_key="abc", grant=_grant(clock, "comment", "abc"))
    assert conn.find_existing("ab") is None and conn.find_existing("abc") is not None


def test_caller_cannot_forge_marker_and_actions_are_validated():
    with pytest.raises(ValidationError):
        Comment(issue_number=1, body="x <!-- patchquest:idempotency:k1 -->")
    with pytest.raises(ValidationError):
        CreatePullRequest(title="t", head="a b", base="main")
    with pytest.raises(ValidationError):
        AddLabel(issue_number=0, label="x")
    with pytest.raises(ValueError, match="owner/name"):
        GitHubConnector("../etc", token_ref=SecretRef("T"), webhook_secret_ref=SecretRef("W"), workspace_id="w")


def test_failure_modes_classified():
    server, clock, conn = _make()
    n = server.add_issue()
    g = _grant(clock, "comment", "k")
    server.script.push(rate_limited("9"))
    with pytest.raises(httpx.HTTPStatusError) as err:  # find_existing hits the 429 first
        conn.perform(Comment(issue_number=n, body="x"), idempotency_key="k", grant=g)
    assert err.value.response.headers["retry-after"] == "9"
    server.script.push(EXPIRED_CREDENTIAL)
    with pytest.raises(httpx.HTTPStatusError) as err:
        conn.perform(Comment(issue_number=n, body="x"), idempotency_key="k", grant=g)
    assert err.value.response.status_code == 401
    server.script.push(TIMEOUT)
    with pytest.raises(httpx.TimeoutException):
        conn.perform(Comment(issue_number=n, body="x"), idempotency_key="k", grant=g)
    with pytest.raises(PatchQuestError) as perm:  # unknown issue: permanent
        conn.perform(Comment(issue_number=999, body="x"), idempotency_key="k", grant=g)
    assert perm.value.kind is FailureKind.TOOL_FAILURE


def test_token_never_leaks(caplog):
    server, clock, conn = _make()
    n = server.add_issue()
    caplog.set_level(logging.DEBUG)
    outputs: list[str] = [repr(conn), str(conn), repr(conn.spec), repr(SecretRef("GH_TOKEN"))]
    g = _grant(clock, "comment", "k")
    result = conn.perform(Comment(issue_number=n, body="hello"), idempotency_key="k", grant=g)
    outputs.append(repr(result))
    for fault in (EXPIRED_CREDENTIAL, rate_limited("1"), TIMEOUT, Fault(status=500)):
        server.script.push(fault)
        try:
            conn.perform(Comment(issue_number=n, body="x"), idempotency_key="k2", grant=_grant(clock, "comment", "k2"))
        except (httpx.HTTPError, PatchQuestError) as exc:
            outputs += [str(exc), repr(exc)]
    wrong = GitHubConnector(REPO, token_ref=SecretRef("GH_TOKEN"), webhook_secret_ref=SecretRef("GH_WH"), workspace_id="w",
                            http=SafeHttp(transport=server.transport(), resolve=resolver()), environ={"GH_TOKEN": "bad", "GH_WH": "w"})
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        wrong.find_existing("k")
    outputs.append(str(exc_info.value))
    missing = GitHubConnector(REPO, token_ref=SecretRef("GH_TOKEN"), webhook_secret_ref=SecretRef("GH_WH"), workspace_id="w",
                              http=SafeHttp(transport=server.transport(), resolve=resolver()), environ={})
    with pytest.raises(PatchQuestError) as miss:
        missing.find_existing("k")
    outputs.append(str(miss.value))
    d = github_delivery(b"whsec", "issues", {"action": "opened", "repository": {"full_name": REPO}, "sender": {"login": "o"},
                                           "issue": {"number": 1, "title": "t", "labels": []}}, "d")
    outputs.append(conn.normalize(d.body, d.headers).to_json())
    outputs.append(caplog.text)
    assert all(SECRET_VALUE not in text for text in outputs)

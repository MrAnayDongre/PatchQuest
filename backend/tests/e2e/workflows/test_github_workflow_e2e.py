"""Signed GitHub webhook -> receiver -> workflow -> agent -> human approval -> comment on GitHub.

MOCKED_PROTOCOL_TEST: GitHub is simulated (``patchquest.connectors.testing``); nothing here has run against live GitHub.
"""

from __future__ import annotations

import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.connectors.base import SecretRef
from patchquest.connectors.github import AddLabel, Comment, CreatePullRequest, GitHubConnector, marker
from patchquest.connectors.migration import apply_fn
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import EXPIRED_CREDENTIAL, Fault, MockGitHub, github_delivery
from patchquest.connectors.webhooks import Accepted, Duplicate, WebhookReceiver
from patchquest.database import get_db
from patchquest.domain.workflows import parse
from patchquest.workflows import store
from patchquest.workflows.catalog import LocalActions
from patchquest.workflows.connector_backend import ConnectorBackend
from patchquest.workflows.engine import TriggerEvent, WorkflowEngine
from patchquest.workflows.templates import instantiate
from tests.e2e.workflows.test_workflow_engine import (
    FIX,
    PLAN,
    REVIEW,
    WS,
    Clock,
    make_calc_repo,
    settle,
    step,
    waiting_at,
)
from tests.unit.connectors.conftest import resolver

pytestmark = pytest.mark.mocked_protocol
SECRET = b"whsec"
REPO = "acme/widgets"


@pytest.fixture(autouse=True)
def setup():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    cfg.agent.promote_policy = "never"
    set_config(cfg)
    with get_db() as conn:
        apply_fn(conn)


@pytest.fixture
def world(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    mock = MockGitHub(REPO, "tok")
    number = mock.add_issue("add() is wrong", "it subtracts")
    connector = GitHubConnector(REPO, token_ref=SecretRef("T"), webhook_secret_ref=SecretRef("W"), workspace_id=WS,
                                http=SafeHttp(transport=mock.transport(), resolve=resolver()), environ={"T": "tok", "W": SECRET.decode()})
    backend = ConnectorBackend(connector, {"comment": Comment, "add_label": AddLabel, "create_pull_request": CreatePullRequest})
    engine = WorkflowEngine(TaskService(), LocalActions({"github": backend}), clock=Clock())
    ScriptedProvider.register("gh-wf", {"planner": [PLAN] * 3, "coder": [FIX] * 3, "reviewer": [REVIEW] * 3})
    with get_db() as conn:
        store.save_version(conn, WS, parse(instantiate("issue-to-proposal", repo=str(repo), provider="scripted", model="gh-wf")), "t")
    return mock, connector, engine, number


def labelled(number, delivery_id="delivery-1"):
    payload = {"action": "labeled", "repository": {"full_name": REPO}, "sender": {"login": "ana"}, "label": {"name": "agent-ready"},
               "issue": {"number": number, "title": "add() is wrong", "body": "it subtracts", "labels": [{"name": "agent-ready"}]}}
    return github_delivery(SECRET, "issues", payload, delivery_id)


async def deliver(rx, connector, engine, delivery):
    received = rx.receive(connector, delivery.headers, delivery.body)
    if not isinstance(received, Accepted):
        return received, []
    runs = await engine.deliver_event(TriggerEvent.from_envelope(received.envelope))
    return received, runs


@pytest.mark.asyncio
async def test_a_labelled_issue_leads_to_one_approved_comment_exactly_once(world):
    mock, connector, engine, number = world
    rx = WebhookReceiver()
    received, runs = await deliver(rx, connector, engine, labelled(number))
    assert isinstance(received, Accepted) and len(runs) == 1
    run_id = runs[0]

    await waiting_at(engine, run_id, "review")
    assert mock.all_comment_bodies() == []  # the agent worked; nothing reached GitHub before a person said yes
    await engine.decide(run_id, "review", "approve", "user:ana")
    await settle(engine, run_id, "completed")

    bodies = mock.all_comment_bodies()
    assert len(bodies) == 1 and "PatchQuest validated a fix" in bodies[0] and marker(f"{run_id}:propose:1") in bodies[0]

    # GitHub redelivers the webhook: a duplicate, so no second run and no second comment
    again, more = await deliver(rx, connector, engine, labelled(number))
    assert isinstance(again, Duplicate) and more == [] and len(mock.all_comment_bodies()) == 1


@pytest.mark.asyncio
async def test_a_lost_response_after_the_comment_landed_does_not_duplicate_it(world):
    mock, connector, engine, number = world
    _, [run_id] = await deliver(WebhookReceiver(), connector, engine, labelled(number))
    await waiting_at(engine, run_id, "review")
    mock.script.push(Fault(status=500, after_effect=True))  # the comment is created, then the response is lost
    await engine.decide(run_id, "review", "approve", "user:ana")
    await settle(engine, run_id, "completed")
    assert len(mock.all_comment_bodies()) == 1  # the retry found its own marker instead of posting again
    assert step(run_id, "propose")["status"] == "succeeded"


@pytest.mark.asyncio
async def test_an_expired_token_fails_the_step_with_a_human_reason_and_posts_nothing(world):
    mock, connector, engine, number = world
    _, [run_id] = await deliver(WebhookReceiver(), connector, engine, labelled(number))
    await waiting_at(engine, run_id, "review")
    mock.script.push(EXPIRED_CREDENTIAL)
    await engine.decide(run_id, "review", "approve", "user:ana")
    run = await settle(engine, run_id, "failed")
    assert "credentials were rejected" in run["error"] and mock.all_comment_bodies() == []


@pytest.mark.asyncio
async def test_the_connector_refuses_a_write_without_the_engines_grant(world):
    """Defence in depth: even called directly, the adapter cannot post without an approver behind it."""
    mock, connector, engine, number = world
    backend = ConnectorBackend(connector, {"comment": Comment})
    with pytest.raises(Exception, match=r"(?i)grant|approval"):
        await backend.perform("github.comment", {"issue_number": number, "body": "hi"}, idempotency_key="k1", approved_by=None)
    assert mock.all_comment_bodies() == []


@pytest.mark.asyncio
async def test_invalid_parameters_are_reported_not_sent(world):
    mock, connector, engine, number = world
    backend = ConnectorBackend(connector, {"comment": Comment})
    with pytest.raises(Exception, match=r"invalid parameters for github\.comment: issue_number"):
        await backend.perform("github.comment", {"issue_number": 0, "body": "hi"}, idempotency_key="k2", approved_by="user:ana")
    with pytest.raises(Exception, match=r"has no action 'delete_repo'"):
        await backend.perform("github.delete_repo", {}, idempotency_key="k3", approved_by="user:ana")
    assert mock.requests == []

"""Webhook receipt end to end: signed delivery -> receiver -> dedup table -> outbound notification.

MOCKED_PROTOCOL_TEST (no marker: pyproject registers none, see tests/unit/connectors/conftest.py): the sender is the simulator in ``patchquest.connectors.testing``; no network.
"""

from __future__ import annotations

import pytest

from patchquest.connectors.base import SecretRef
from patchquest.connectors.github import GitHubConnector
from patchquest.connectors.migration import apply_fn
from patchquest.connectors.outbound import OutboundWebhooks
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import ScriptedEndpoint, duplicated, github_delivery
from patchquest.connectors.webhooks import Accepted, Duplicate, Rejected, WebhookReceiver
from patchquest.database import get_db
from tests.support.db import insert_run
from tests.unit.connectors.conftest import resolver


@pytest.fixture(autouse=True)
def _tables(isolated_runtime):
    with get_db() as conn:
        apply_fn(conn)


def test_signed_delivery_to_run_to_outbound_notification():
    conn = GitHubConnector("acme/widgets", token_ref=SecretRef("T"), webhook_secret_ref=SecretRef("W"), workspace_id="ws",
                           environ={"W": "whsec", "T": "tok"})
    payload = {"action": "opened", "repository": {"full_name": "acme/widgets"}, "sender": {"login": "o"},
               "issue": {"number": 7, "title": "boom", "labels": []}}
    first, second = duplicated(github_delivery(b"whsec", "issues", payload, "evt-1"))
    rx = WebhookReceiver()
    accepted = rx.receive(conn, first.headers, first.body)
    assert isinstance(accepted, Accepted) and accepted.envelope.payload["number"] == 7

    insert_run("run-1")  # the orchestrator would create this run
    rx.record_outcome("ws", "github", "evt-1", "STARTED", run_id="run-1")
    assert rx.receive(conn, second.headers, second.body) == Duplicate("github", "evt-1", "STARTED", "run-1")
    forged = github_delivery(b"nope", "issues", payload, "evt-2")
    assert rx.receive(conn, forged.headers, forged.body) == Rejected("invalid_signature")

    endpoint = ScriptedEndpoint()
    hooks = OutboundWebhooks(SecretRef("O"), http=SafeHttp(transport=endpoint.transport(), resolve=resolver()), environ={"O": "k"})
    hooks.enqueue("https://hooks.example.com/x", "run.started", {"run_id": "run-1"})
    assert [d.status for d in hooks.deliver_due()] == ["DELIVERED"]
    with get_db() as db:
        assert db.execute("SELECT run_id FROM connector_events WHERE external_id = 'evt-1'").fetchone()[0] == "run-1"

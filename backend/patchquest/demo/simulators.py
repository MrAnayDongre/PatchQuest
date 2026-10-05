"""Simulated GitHub and Slack for the demo: the real connectors, talking to in-process fakes. Never to the real services."""

from __future__ import annotations

import socket
from typing import Any

import httpx

from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.testing.mock_server import Delivery, MockGitHub, MockSlack, github_delivery

REPO = "acme/payments-service"
CHANNEL = "C0DEMO001"
GITHUB_TOKEN, SLACK_TOKEN = "demo-github-token", "demo-slack-token"
GITHUB_WEBHOOK_SECRET, SLACK_SIGNING_SECRET = "demo-github-webhook-secret", "demo-slack-signing-secret"
ENV = {"PATCHQUEST_DEMO_GITHUB_TOKEN": GITHUB_TOKEN, "PATCHQUEST_DEMO_GITHUB_WEBHOOK": GITHUB_WEBHOOK_SECRET,
       "PATCHQUEST_DEMO_SLACK_TOKEN": SLACK_TOKEN, "PATCHQUEST_DEMO_SLACK_SIGNING": SLACK_SIGNING_SECRET}


def _public(host: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class Simulators:
    def __init__(self) -> None:
        self.github = MockGitHub(REPO, GITHUB_TOKEN)
        self.slack = MockSlack(SLACK_TOKEN, [CHANNEL])
        self._hosts: dict[str, Any] = {"api.github.com": self.github, "slack.com": self.slack}

    def _handler(self, request: httpx.Request) -> httpx.Response:
        return self._hosts[request.url.host].handler(request)

    def http(self) -> SafeHttp:
        return SafeHttp(transport=httpx.MockTransport(self._handler), resolve=_public)

    def open_issue(self, title: str, body: str) -> int:
        return self.github.add_issue(title, body)

    def labeled_event(self, number: int, title: str, body: str, delivery_id: str, label: str = "agent-ready") -> Delivery:
        """What GitHub would send when someone labels the issue (``agent-ready`` unless a workflow listens for another label)."""
        payload = {"action": "labeled", "repository": {"full_name": REPO}, "sender": {"login": "ana-demo"}, "label": {"name": label},
                   "issue": {"number": number, "title": title, "body": body, "labels": [{"name": label}]}}
        return github_delivery(GITHUB_WEBHOOK_SECRET.encode(), "issues", payload, delivery_id)

    def transcript(self) -> dict[str, list[str]]:
        return {"github_comments": self.github.all_comment_bodies(), "slack_messages": self.slack.texts()}

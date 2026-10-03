"""Jira Cloud connector (REST v3; admin webhooks signed with ``X-Hub-Signature``).

MOCKED_PROTOCOL: tested against ``testing.mock_server.MockJira``; never run against live Jira.

* Triggers: ``issue_created``, ``issue_updated``, ``comment_created`` for one project.
* Actions: ``comment`` on an issue (Atlassian Document Format). Transitions, assignment and field edits are not offered.
* Idempotency: the comment text carries ``patchquest:idempotency:KEY``; ``find_existing`` searches the project's
  comments with JQL (``comment ~ ...``), then reads the comments of each hit. Jira's text index is eventually consistent.
* The site URL is operator configuration and passes the SSRF guard on every request.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from patchquest.connectors.base import (
    Action,
    ActionResult,
    ActionSpec,
    Connector,
    ConnectorSpec,
    MalformedEvent,
    SecretRef,
    UnsupportedEvent,
)
from patchquest.connectors.clock import Clock, utc_now
from patchquest.connectors.envelope import EventEnvelope, SignatureStatus
from patchquest.connectors.github import MARKER_PREFIX
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.webhooks import _matches
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.tools.secret_guard import redact_secrets

TRIGGERS = ("issue_created", "issue_updated", "comment_created")
_EVENTS = {"jira:issue_created": "issue_created", "jira:issue_updated": "issue_updated", "comment_created": "comment_created"}
_PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{1,20}$")
_ISSUE = re.compile(r"^[A-Z][A-Z0-9_]{1,20}-\d{1,9}$")


class Comment(Action):
    name = "comment"
    issue_key: str = Field(pattern=_ISSUE.pattern)
    body: str = Field(min_length=1, max_length=30000)


SPEC = ConnectorSpec(name="jira", version="1", triggers=TRIGGERS, actions=(ActionSpec("comment", SideEffect.EXTERNAL_WRITE),),
                     required_scopes=("write:comment:jira", "read:jira-work"))


def _adf(text: str) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]}
                                                    for line in text.split("\n") if line.strip()] or [{"type": "paragraph", "content": []}]}


def _adf_text(node: Any) -> str:
    if isinstance(node, dict):
        return (node.get("text", "") if node.get("type") == "text" else "") + "\n".join(_adf_text(c) for c in node.get("content", []))
    return ""


class JiraConnector(Connector):
    spec = SPEC

    def __init__(self, *, workspace_id: str, base_url: str, project_key: str, email_ref: SecretRef, api_token_ref: SecretRef,
                 webhook_secret_ref: SecretRef, http: SafeHttp | None = None, clock: Clock = utc_now,
                 environ: Mapping[str, str] | None = None) -> None:
        super().__init__(clock)
        if not _PROJECT.match(project_key):
            raise ValueError("project_key must look like 'PROJ'")
        if not base_url.startswith("https://"):
            raise ValueError("base_url must be an https:// Jira site")
        self.workspace_id, self.project_key, self._base = workspace_id, project_key, base_url.rstrip("/")
        self._email_ref, self._token_ref, self._secret_ref, self._environ = email_ref, api_token_ref, webhook_secret_ref, environ
        self._http = http or SafeHttp()

    def __repr__(self) -> str:
        return f"JiraConnector(site={self._base!r}, project={self.project_key!r})"

    def verify(self, headers: Mapping[str, str], body: bytes) -> SignatureStatus:
        presented = {k.lower(): v for k, v in headers.items()}.get("x-hub-signature")
        if presented is None:
            return SignatureStatus.UNVERIFIED
        if not presented.startswith("sha256="):
            return SignatureStatus.INVALID
        ok = _matches(self._secret_ref.resolve(self._environ).encode(), body, presented[len("sha256="):])
        return SignatureStatus.VERIFIED if ok else SignatureStatus.INVALID

    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> EventEnvelope:
        delivery = {k.lower(): v for k, v in headers.items()}.get("x-atlassian-webhook-identifier")
        if not delivery:
            raise MalformedEvent("missing X-Atlassian-Webhook-Identifier")
        try:
            data = json.loads(raw)
            etype = _EVENTS.get(data["webhookEvent"])
            if etype is None:
                raise UnsupportedEvent(str(data["webhookEvent"]))
            issue = data["issue"]
            if issue["fields"]["project"]["key"] != self.project_key:
                raise UnsupportedEvent("event is for a different project")
            payload: dict[str, Any] = {"key": issue["key"], "summary": str(issue["fields"].get("summary", ""))[:256],
                                       "description": _adf_text(issue["fields"].get("description")) if isinstance(issue["fields"].get("description"), dict)
                                       else str(issue["fields"].get("description") or "")[:4000],
                                       "labels": [str(x) for x in issue["fields"].get("labels", [])],
                                       "status": (issue["fields"].get("status") or {}).get("name"),
                                       "url": f"{self._base}/browse/{issue['key']}"}
            payload["description"] = payload["description"][:4000]
            if etype == "comment_created":
                comment = data["comment"]
                payload["comment"] = (_adf_text(comment["body"]) if isinstance(comment.get("body"), dict) else str(comment.get("body", "")))[:4000]
            actor = (data.get("user") or {}).get("displayName")
            return EventEnvelope(source="jira", type=etype, external_id=delivery,
                                 timestamp=datetime.fromtimestamp(int(data.get("timestamp", self._clock().timestamp() * 1000)) / 1000, UTC),
                                 workspace_id=self.workspace_id, actor=actor, payload=payload, signature_status=SignatureStatus.VERIFIED)
        except UnsupportedEvent:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise MalformedEvent("unexpected payload shape") from None

    def _call(self, method: str, path: str, *, body: Mapping[str, Any] | None = None, params: Mapping[str, str] | None = None) -> Any:
        creds = base64.b64encode(f"{self._email_ref.resolve(self._environ)}:{self._token_ref.resolve(self._environ)}".encode()).decode()
        headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}
        content = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(body).encode()
        response = self._http.request(method, f"{self._base}{path}", headers=headers, content=content, params=params)
        try:
            return response.json()
        except ValueError:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "Jira returned a non-JSON body") from None

    def check(self, action: Action) -> None:
        if isinstance(action, Comment) and not action.issue_key.startswith(self.project_key + "-"):
            raise PatchQuestError(FailureKind.POLICY_DENIED, f"{action.issue_key} is not in project {self.project_key}")

    def _execute(self, action: Action, idempotency_key: str) -> ActionResult:
        if not isinstance(action, Comment):
            raise ValueError(f"unsupported action {type(action).__name__}")
        self.check(action)
        text = f"{redact_secrets(action.body)}\n{MARKER_PREFIX}{idempotency_key}"
        try:
            data = self._call("POST", f"/rest/api/3/issue/{action.issue_key}/comment", body={"body": _adf(text)})
            return ActionResult(str(data["id"]), f"{self._base}/browse/{action.issue_key}?focusedCommentId={data['id']}")
        except (KeyError, TypeError):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Jira response shape") from None

    def health(self) -> str:
        data = self._call("GET", "/rest/api/3/myself")
        return f"authenticated as {data.get('displayName', 'the API user')}"

    def find_existing(self, idempotency_key: str) -> ActionResult | None:
        needle = f"{MARKER_PREFIX}{idempotency_key}"
        found = self._call("GET", "/rest/api/3/search", params={"jql": f'project = {self.project_key} AND comment ~ "\\"{needle}\\""', "fields": "key"})
        try:
            for issue in found["issues"]:
                comments = self._call("GET", f"/rest/api/3/issue/{issue['key']}/comment")
                for comment in comments["comments"]:
                    if needle in _adf_text(comment.get("body")):
                        return ActionResult(str(comment["id"]), f"{self._base}/browse/{issue['key']}?focusedCommentId={comment['id']}", created=False)
        except (KeyError, TypeError):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Jira search response") from None
        return None

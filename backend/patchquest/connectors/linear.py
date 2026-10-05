"""Linear connector (GraphQL API; webhooks signed with ``Linear-Signature``).

MOCKED_PROTOCOL: tested against ``testing.mock_server.MockLinear``; never run against live Linear.

* Triggers: ``Issue.create``, ``Issue.update``, ``Comment.create``. Webhook timestamps older than 60 s are refused
  (Linear's documented replay window).
* Actions: ``comment`` on an issue - deliberately the only write. Moving issues between states, reassigning or
  deleting are not offered, so an agent cannot rearrange a team's board.
* Idempotency: the comment carries ``<!-- patchquest:idempotency:KEY -->``; ``find_existing`` filters comments by it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
from patchquest.connectors.github import MARKER_PREFIX, marker
from patchquest.connectors.ssrf import SafeHttp
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.tools.secret_guard import redact_secrets

TRIGGERS = ("Issue.create", "Issue.update", "Comment.create")
REPLAY_WINDOW_S = 60
_COMMENT = "mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success comment { id url } } }"
_FIND = "query($f: CommentFilter) { comments(filter: $f, first: 5) { nodes { id url body } } }"


class Comment(Action):
    name = "comment"
    issue_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    body: str = Field(min_length=1, max_length=20000)


SPEC = ConnectorSpec(name="linear", version="1", triggers=TRIGGERS, actions=(ActionSpec("comment", SideEffect.EXTERNAL_WRITE),),
                     required_scopes=("comments:create", "read"))


class LinearConnector(Connector):
    spec = SPEC

    def __init__(self, *, workspace_id: str, api_key_ref: SecretRef, webhook_secret_ref: SecretRef, team_key: str | None = None,
                 http: SafeHttp | None = None, api_url: str = "https://api.linear.app/graphql", clock: Clock = utc_now,
                 environ: Mapping[str, str] | None = None) -> None:
        super().__init__(clock)
        self.workspace_id, self.team_key = workspace_id, team_key
        self._key_ref, self._secret_ref, self._environ = api_key_ref, webhook_secret_ref, environ
        self._http, self._url = http or SafeHttp(), api_url

    def __repr__(self) -> str:
        return f"LinearConnector(team={self.team_key!r}, key_ref={self._key_ref!r})"

    def verify(self, headers: Mapping[str, str], body: bytes) -> SignatureStatus:
        presented = {k.lower(): v for k, v in headers.items()}.get("linear-signature")
        if presented is None:
            return SignatureStatus.UNVERIFIED
        expected = hmac.new(self._secret_ref.resolve(self._environ).encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.encode(), presented.encode("utf-8", "replace")):
            return SignatureStatus.INVALID
        try:  # the signature covers the body, which carries the send time: refuse stale deliveries
            sent = int(json.loads(body)["webhookTimestamp"]) / 1000
        except (ValueError, KeyError, TypeError):
            return SignatureStatus.INVALID
        return SignatureStatus.VERIFIED if abs(self._clock().timestamp() - sent) <= REPLAY_WINDOW_S else SignatureStatus.INVALID

    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> EventEnvelope:
        lowered = {k.lower(): v for k, v in headers.items()}
        delivery = lowered.get("linear-delivery")
        if not delivery:
            raise MalformedEvent("missing Linear-Delivery")
        try:
            data = json.loads(raw)
            etype = f"{data['type']}.{data['action']}"
            if etype not in TRIGGERS:
                raise UnsupportedEvent(etype)
            obj = data["data"]
            team = (obj.get("team") or {}).get("key")
            if self.team_key and team != self.team_key:
                raise UnsupportedEvent("event is for a different team")
            payload: dict[str, Any] = {"id": obj["id"], "team": team, "url": data.get("url")}
            if data["type"] == "Issue":
                payload.update(identifier=obj.get("identifier"), title=str(obj.get("title", ""))[:256],
                               description=str(obj.get("description") or "")[:4000], labels=[str(x.get("name")) for x in obj.get("labels", [])],
                               state=(obj.get("state") or {}).get("name"))
            else:
                payload.update(issue_id=obj.get("issueId"), body=str(obj.get("body", ""))[:4000])
            return EventEnvelope(source="linear", type=etype, external_id=delivery,
                                 timestamp=datetime.fromtimestamp(int(data["webhookTimestamp"]) / 1000, UTC),
                                 workspace_id=self.workspace_id, actor=(data.get("actor") or {}).get("name"), payload=payload,
                                 signature_status=SignatureStatus.VERIFIED)
        except UnsupportedEvent:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise MalformedEvent("unexpected payload shape") from None

    def _graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": self._key_ref.resolve(self._environ), "Content-Type": "application/json"}
        response = self._http.request("POST", self._url, headers=headers, content=json.dumps({"query": query, "variables": variables}).encode())
        try:
            data = response.json()
        except ValueError:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "Linear returned a non-JSON body") from None
        for err in (data.get("errors") or []) if isinstance(data, dict) else []:
            kind = str((err.get("extensions") or {}).get("type", "")).lower()
            if "authentication" in kind or "forbidden" in kind:
                raise PatchQuestError(FailureKind.CONNECTOR_AUTH, "Linear rejected the credentials")
            if "ratelimit" in kind:
                raise PatchQuestError(FailureKind.CONNECTOR_RATE_LIMIT, "Linear is rate limiting requests")
            raise PatchQuestError(FailureKind.TOOL_FAILURE, f"Linear refused the request ({kind or 'error'})")
        if not isinstance(data, dict) or "data" not in data:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Linear response")
        return data["data"]

    def _execute(self, action: Action, idempotency_key: str) -> ActionResult:
        if not isinstance(action, Comment):
            raise ValueError(f"unsupported action {type(action).__name__}")
        data = self._graphql(_COMMENT, {"input": {"issueId": action.issue_id, "body": f"{redact_secrets(action.body)}\n\n{marker(idempotency_key)}"}})
        try:
            created = data["commentCreate"]
            if not created["success"]:
                raise PatchQuestError(FailureKind.TOOL_FAILURE, "Linear did not create the comment")
            return ActionResult(str(created["comment"]["id"]), created["comment"].get("url"))
        except (KeyError, TypeError):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Linear response shape") from None

    def health(self) -> str:
        data = self._graphql("query { viewer { id name } }", {})
        return f"authenticated as {data['viewer']['name']}"

    def find_existing(self, idempotency_key: str) -> ActionResult | None:
        data = self._graphql(_FIND, {"f": {"body": {"contains": f"{MARKER_PREFIX}{idempotency_key}"}}})
        try:
            for node in data["comments"]["nodes"]:
                if marker(idempotency_key) in (node.get("body") or ""):
                    return ActionResult(str(node["id"]), node.get("url"), created=False)
        except (KeyError, TypeError):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Linear search response") from None
        return None

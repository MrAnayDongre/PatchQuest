"""The generic webhook connector: any system that can sign an HTTP request can start a workflow, and a workflow can
call a pre-registered endpoint.

Inbound (``POST /hooks/<integration id>``): the sender signs ``<unix timestamp>.<raw body>`` with the shared secret
(HMAC-SHA256) and sends ``X-PatchQuest-Timestamp``, ``X-PatchQuest-Signature: sha256=<hex>``,
``X-PatchQuest-Delivery`` (a unique id, used for dedup) and ``X-PatchQuest-Event`` (the event type). Deliveries more
than five minutes from now, in either direction, are refused. Only event types the operator listed are accepted.

Outbound (``post``): to a *named endpoint* the operator registered in the integration (the workflow chooses the name
and the JSON body, never the URL). Every request passes the SSRF guard. It is not idempotent: if a crash leaves it
uncertain the workflow step is marked ``uncertain`` rather than sent again.
"""

from __future__ import annotations

import hashlib
import hmac
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
from patchquest.connectors.ssrf import SafeHttp
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError

TOLERANCE_S = 300
MAX_POST_BYTES = 64 * 1024
_EVENT = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_ENDPOINT = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class Post(Action):
    name = "post"
    endpoint: str = Field(pattern=_ENDPOINT.pattern)
    body: dict[str, Any]


SPEC = ConnectorSpec(name="webhook", version="1", triggers=("*",), actions=(ActionSpec("post", SideEffect.EXTERNAL_WRITE),))


def sign(secret: bytes, timestamp: int, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


class WebhookConnector(Connector):
    spec = SPEC

    def __init__(self, *, workspace_id: str, signing_secret_ref: SecretRef, event_types: tuple[str, ...],
                 endpoints: Mapping[str, str] | None = None, post_secret_ref: SecretRef | None = None, http: SafeHttp | None = None,
                 clock: Clock = utc_now, environ: Mapping[str, str] | None = None) -> None:
        super().__init__(clock)
        if not event_types or not all(_EVENT.match(t) for t in event_types):
            raise ValueError("list the event types this webhook may deliver (lowercase letters, digits, '.', '_', '-')")
        self.workspace_id, self.event_types = workspace_id, tuple(event_types)
        self.endpoints = dict(endpoints or {})
        if not all(_ENDPOINT.match(n) and u.startswith("https://") for n, u in self.endpoints.items()):
            raise ValueError("endpoints are 'name: https://url'")
        self._secret_ref, self._post_ref, self._environ = signing_secret_ref, post_secret_ref, environ
        self._http = http or SafeHttp()

    def __repr__(self) -> str:
        return f"WebhookConnector(events={self.event_types!r}, endpoints={sorted(self.endpoints)!r})"

    def verify(self, headers: Mapping[str, str], body: bytes) -> SignatureStatus:
        lowered = {k.lower(): v for k, v in headers.items()}
        stamp, presented = lowered.get("x-patchquest-timestamp"), lowered.get("x-patchquest-signature")
        if stamp is None or presented is None:
            return SignatureStatus.UNVERIFIED
        try:
            when = int(stamp)
        except ValueError:
            return SignatureStatus.INVALID
        if abs(self._clock().timestamp() - when) > TOLERANCE_S:
            return SignatureStatus.INVALID
        expected = sign(self._secret_ref.resolve(self._environ).encode(), when, body)
        return SignatureStatus.VERIFIED if hmac.compare_digest(expected.encode(), presented.encode("utf-8", "replace")) else SignatureStatus.INVALID

    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> EventEnvelope:
        lowered = {k.lower(): v for k, v in headers.items()}
        event, delivery = lowered.get("x-patchquest-event"), lowered.get("x-patchquest-delivery")
        if not event or not delivery:
            raise MalformedEvent("missing X-PatchQuest-Event or X-PatchQuest-Delivery")
        if not _EVENT.match(event):
            raise MalformedEvent("invalid event type")
        if event not in self.event_types:
            raise UnsupportedEvent(event)
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            raise MalformedEvent("body is not JSON") from None
        if not isinstance(data, dict):
            raise MalformedEvent("body is not a JSON object")
        return EventEnvelope(source="webhook", type=event, external_id=delivery[:256],
                             timestamp=datetime.fromtimestamp(int(lowered["x-patchquest-timestamp"]), UTC), workspace_id=self.workspace_id,
                             actor=str(data.get("actor"))[:256] if data.get("actor") else None, payload=data,
                             signature_status=SignatureStatus.VERIFIED)

    def find_existing(self, idempotency_key: str) -> ActionResult | None:
        return None  # a plain HTTP post has no record to look up

    def check(self, action: Action) -> None:
        if isinstance(action, Post) and action.endpoint not in self.endpoints:
            raise PatchQuestError(FailureKind.POLICY_DENIED, f"'{action.endpoint}' is not an endpoint registered on this integration")

    def _execute(self, action: Action, idempotency_key: str) -> ActionResult:
        if not isinstance(action, Post):
            raise ValueError(f"unsupported action {type(action).__name__}")
        self.check(action)
        url = self.endpoints[action.endpoint]
        body = json.dumps(action.body, separators=(",", ":")).encode()
        if len(body) > MAX_POST_BYTES:
            raise PatchQuestError(FailureKind.TOOL_FAILURE, f"the body is larger than {MAX_POST_BYTES} bytes")
        headers = {"Content-Type": "application/json", "Idempotency-Key": idempotency_key, "X-PatchQuest-Delivery": idempotency_key}
        if self._post_ref is not None:
            stamp = int(self._clock().timestamp())
            headers.update({"X-PatchQuest-Timestamp": str(stamp), "X-PatchQuest-Signature": sign(self._post_ref.resolve(self._environ).encode(), stamp, body)})
        response = self._http.request("POST", url, headers=headers, content=body)
        return ActionResult(f"{action.endpoint}:{response.status_code}:{idempotency_key}")

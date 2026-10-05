"""Inbound webhooks: size limit, HMAC verification, normalisation, then dedup by (source, external_id).

Order matters: nothing is parsed before the signature passes and nothing is stored unless the delivery
is authentic and well formed, so an attacker cannot burn dedup ids or fill the table. Redelivery of an
accepted event returns the original outcome; the primary key makes concurrent double delivery store once.

Ordering: providers do not deliver in order. Dedup is order-independent; consumers that care compare
``envelope.timestamp`` (never arrival order).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass

from patchquest.connectors.base import Connector, MalformedEvent, UnsupportedEvent
from patchquest.connectors.clock import Clock, utc_iso, utc_now
from patchquest.connectors.envelope import EventEnvelope, SignatureStatus
from patchquest.database import get_db

MAX_BODY_BYTES = 1 << 20
SLACK_TOLERANCE_S = 300
EVENT_STATUSES = frozenset({"RECEIVED", "STARTED", "IGNORED"})


def _hmac_hex(secret: bytes, message: bytes) -> str:
    if not secret:
        raise ValueError("an empty signing secret would make signatures forgeable")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _matches(secret: bytes, message: bytes, presented: str) -> bool:
    """Constant-time comparison; encoding first so non-ASCII input cannot raise or short-circuit."""
    return hmac.compare_digest(_hmac_hex(secret, message).encode(), presented.encode("utf-8", "replace"))


def verify_github_signature(secret: bytes, headers: Mapping[str, str], body: bytes) -> SignatureStatus:
    """``X-Hub-Signature-256: sha256=<hex>`` over the raw body."""
    presented = {k.lower(): v for k, v in headers.items()}.get("x-hub-signature-256")
    if presented is None:
        return SignatureStatus.UNVERIFIED
    if not presented.startswith("sha256="):
        return SignatureStatus.INVALID
    ok = _matches(secret, body, presented[len("sha256="):])
    return SignatureStatus.VERIFIED if ok else SignatureStatus.INVALID


def verify_slack_signature(secret: bytes, headers: Mapping[str, str], body: bytes, *, now: float,
                           tolerance_s: int = SLACK_TOLERANCE_S) -> SignatureStatus:
    """``X-Slack-Signature: v0=<hex>`` over ``v0:<timestamp>:<body>``; the timestamp must be within
    ``tolerance_s`` of ``now`` in *both* directions (a far-future stamp would be replayable for a long time)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    stamp, presented = lowered.get("x-slack-request-timestamp"), lowered.get("x-slack-signature")
    if stamp is None or presented is None:
        return SignatureStatus.UNVERIFIED
    try:
        when = int(stamp)
    except ValueError:
        return SignatureStatus.INVALID
    if abs(now - when) > tolerance_s or not presented.startswith("v0="):
        return SignatureStatus.INVALID
    base = b"v0:" + stamp.encode() + b":" + body
    return SignatureStatus.VERIFIED if _matches(secret, base, presented[len("v0="):]) else SignatureStatus.INVALID


@dataclass(frozen=True)
class Accepted:
    envelope: EventEnvelope


@dataclass(frozen=True)
class Duplicate:
    source: str
    external_id: str
    status: str  # the original outcome
    run_id: str | None


@dataclass(frozen=True)
class Rejected:
    reason: str  # body_too_large | unverified_signature | invalid_signature | malformed | unsupported_event


Received = Accepted | Duplicate | Rejected


class WebhookReceiver:
    def __init__(self, *, max_body_bytes: int = MAX_BODY_BYTES, clock: Clock = utc_now) -> None:
        self._max_body, self._clock = max_body_bytes, clock

    def receive(self, connector: Connector, headers: Mapping[str, str], body: bytes) -> Received:
        if len(body) > self._max_body:
            return Rejected("body_too_large")
        status = connector.verify(headers, body)
        if status is SignatureStatus.UNVERIFIED:
            return Rejected("unverified_signature")
        if status is SignatureStatus.INVALID:
            return Rejected("invalid_signature")
        try:
            envelope = connector.normalize(body, headers)
        except UnsupportedEvent:
            return Rejected("unsupported_event")
        except MalformedEvent:
            return Rejected("malformed")
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO connector_events (workspace_id, source, external_id, received_at, status) "
                "VALUES (?, ?, ?, ?, 'RECEIVED') ON CONFLICT(workspace_id, source, external_id) DO NOTHING",
                (envelope.workspace_id, envelope.source, envelope.external_id, utc_iso(self._clock())))
            if cursor.rowcount == 1:
                return Accepted(envelope)
            row = conn.execute("SELECT status, run_id FROM connector_events WHERE workspace_id = ? AND source = ? AND external_id = ?",
                               (envelope.workspace_id, envelope.source, envelope.external_id)).fetchone()
        return Duplicate(envelope.source, envelope.external_id, row["status"], row["run_id"])

    def record_outcome(self, workspace_id: str, source: str, external_id: str, status: str, run_id: str | None = None) -> None:
        if status not in EVENT_STATUSES:
            raise ValueError(f"unknown event status {status!r}")
        with get_db() as conn:
            updated = conn.execute("UPDATE connector_events SET status = ?, run_id = ? WHERE workspace_id = ? AND source = ? AND external_id = ?",
                                   (status, run_id, workspace_id, source, external_id)).rowcount
        if updated != 1:
            raise KeyError(f"no stored event {workspace_id}/{source}/{external_id}")

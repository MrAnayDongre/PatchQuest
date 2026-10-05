"""Outgoing webhooks: signed, durable, retried by the central engine, dead-lettered after its cap.

Each delivery carries a stable ``X-PatchQuest-Delivery`` id so receivers can dedupe; that is what makes
a repeat after an ambiguous timeout safe (``idempotent=True`` to ``decide``). An attempt is counted when
it is *claimed*, so a crash mid-send can cost an attempt but can never loop forever.

Signature: ``X-PatchQuest-Signature: sha256=<hex>`` = HMAC-SHA256(secret, ``<timestamp>.<body>``) with the
timestamp in ``X-PatchQuest-Timestamp``; receivers should apply a replay window as ``webhooks.py`` does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx

from patchquest.connectors.base import SecretRef
from patchquest.connectors.clock import Clock, utc_iso, utc_now
from patchquest.connectors.ssrf import SafeHttp
from patchquest.database import get_db
from patchquest.domain.failures import Origin, PatchQuestError, classify
from patchquest.runtime.retry import RetryPolicy, decide
from patchquest.tools.secret_guard import redact_secrets

CLAIM_LEASE = timedelta(minutes=5)  # a crashed worker's claim becomes due again after this
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class Delivery:
    id: str
    url: str
    event_type: str
    attempts: int
    next_attempt_at: str
    status: str
    last_error: str | None


class OutboundWebhooks:
    def __init__(self, secret_ref: SecretRef, *, http: SafeHttp | None = None, clock: Clock = utc_now,
                 rng: random.Random | None = None, policy: RetryPolicy | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        self._secret_ref, self._http, self._clock, self._rng = secret_ref, http or SafeHttp(), clock, rng
        self._policy, self._environ = policy, environ

    def __repr__(self) -> str:
        return f"OutboundWebhooks(secret_ref={self._secret_ref!r})"

    def enqueue(self, url: str, event_type: str, body: Mapping[str, Any]) -> str:
        """Validate now (fail fast on a bad URL or missing secret); re-validated at every send."""
        self._http.check(url)
        self._secret_ref.resolve(self._environ)
        text = json.dumps(body, separators=(",", ":"), sort_keys=True)
        if len(text.encode()) > MAX_BODY_BYTES:
            raise ValueError(f"webhook body exceeds {MAX_BODY_BYTES} bytes")
        delivery_id, now = uuid.uuid4().hex, utc_iso(self._clock())
        with get_db() as conn:
            conn.execute("INSERT INTO webhook_deliveries (id, url, event_type, body_json, next_attempt_at, status, created_at) "
                         "VALUES (?, ?, ?, ?, ?, 'PENDING', ?)", (delivery_id, url, event_type, text, now, now))
        return delivery_id

    def get(self, delivery_id: str) -> Delivery:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,)).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        return Delivery(row["id"], row["url"], row["event_type"], row["attempts"], row["next_attempt_at"],
                        row["status"], row["last_error"])

    def deliver_due(self, limit: int = 50) -> list[Delivery]:
        """Attempt every PENDING delivery that is due; returns their state afterwards."""
        now = self._clock()
        with get_db() as conn:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM webhook_deliveries WHERE status = 'PENDING' AND next_attempt_at <= ? "
                "ORDER BY next_attempt_at LIMIT ?", (utc_iso(now), limit))]
        return [self.get(i) for i in ids if self._attempt(i)]

    def _attempt(self, delivery_id: str) -> bool:
        now = self._clock()
        with get_db() as conn:  # claim: only one worker wins, and the attempt is counted before any I/O
            claimed = conn.execute(
                "UPDATE webhook_deliveries SET attempts = attempts + 1, next_attempt_at = ? "
                "WHERE id = ? AND status = 'PENDING' AND next_attempt_at <= ?",
                (utc_iso(now + CLAIM_LEASE), delivery_id, utc_iso(now))).rowcount
            row = conn.execute("SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,)).fetchone()
        if claimed != 1:
            return False
        try:
            self._send(row["id"], row["url"], row["event_type"], row["body_json"])
        except (httpx.HTTPError, PatchQuestError) as exc:
            failure = classify(exc, origin=Origin.CONNECTOR)
            decision = decide(failure, row["attempts"], policy=self._policy, idempotent=True, rng=self._rng)
            error = redact_secrets(f"{failure.kind.value}: {failure.detail}")[:500]
            if decision.retry:
                self._finish(delivery_id, "PENDING", error, utc_iso(self._clock() + timedelta(seconds=decision.delay_s)))
            else:
                self._finish(delivery_id, "DEAD", f"{error} ({decision.reason})", None)
        else:
            self._finish(delivery_id, "DELIVERED", None, None)
        return True

    def _finish(self, delivery_id: str, status: str, error: str | None, next_at: str | None) -> None:
        with get_db() as conn:
            conn.execute("UPDATE webhook_deliveries SET status = ?, last_error = ?, next_attempt_at = COALESCE(?, next_attempt_at) "
                         "WHERE id = ?", (status, error, next_at, delivery_id))

    def _send(self, delivery_id: str, url: str, event_type: str, body_json: str) -> None:
        secret = self._secret_ref.resolve(self._environ).encode()
        stamp = str(int(self._clock().timestamp()))
        body = body_json.encode()
        signature = hmac.new(secret, stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        self._http.request("POST", url, content=body, headers={
            "Content-Type": "application/json", "X-PatchQuest-Delivery": delivery_id, "X-PatchQuest-Event": event_type,
            "X-PatchQuest-Timestamp": stamp, "X-PatchQuest-Signature": f"sha256={signature}"})

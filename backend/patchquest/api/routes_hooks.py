"""The public endpoint external systems call: ``POST /hooks/<integration id>``.

There is no bearer token here; the sender is authenticated by the integration's webhook signature, checked before
anything is parsed or stored. Unknown, disabled and signature-less requests all look the same from outside. A
well-formed delivery is deduplicated per workspace, normalised, and handed to the workflow engine; events that are
valid but not triggers (a bot's own message, another channel) are acknowledged and ignored so the sender stops retrying.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from patchquest.connectors.envelope import SignatureStatus
from patchquest.connectors.webhooks import MAX_BODY_BYTES, Accepted, Duplicate, Rejected, WebhookReceiver
from patchquest.database import get_db
from patchquest.integrations import service
from patchquest.persistence import identity
from patchquest.workflows.engine import TriggerEvent
from patchquest.workflows.runtime import get_engine

router = APIRouter(tags=["hooks"])

_WINDOW_S, _LIMIT = 60.0, 300
_hits: dict[str, list[float]] = {}
_lock = threading.Lock()
_STATUS = {"body_too_large": 413, "unverified_signature": 401, "invalid_signature": 401, "malformed": 400}


def _limited(integration_id: str) -> bool:
    """A fixed window per integration so a flood cannot grow the database or the workflow queue without bound."""
    now = time.monotonic()
    with _lock:
        hits = [t for t in _hits.get(integration_id, []) if now - t < _WINDOW_S]
        hits.append(now)
        _hits[integration_id] = hits
        if len(_hits) > 10_000:  # bound the table itself
            _hits.clear()
        return len(hits) > _LIMIT


async def _read(request: Request) -> bytes | None:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def _not_found() -> JSONResponse:
    return JSONResponse({"status": "not_found"}, status_code=404)


@router.post("/hooks/{integration_id}", include_in_schema=False)
async def receive(integration_id: str, request: Request) -> JSONResponse:
    if _limited(integration_id):
        return JSONResponse({"status": "rate_limited"}, status_code=429, headers={"Retry-After": "30"})
    with get_db() as conn:
        found = conn.execute("SELECT id, workspace_id FROM integrations WHERE id = ? AND status != 'disabled'", (integration_id,)).fetchone()
        row = service.get(conn, found["workspace_id"], integration_id) if found else None
    if row is None or not service.kind_of(row["kind"]).inbound:
        return _not_found()
    body = await _read(request)
    if body is None:
        return JSONResponse({"status": "rejected", "reason": "body_too_large"}, status_code=413)
    headers = dict(request.headers)
    try:
        connector = service.connector_for(row)
        if connector.verify(headers, body) is SignatureStatus.VERIFIED:
            reply = connector.handshake(headers, body)
            if reply is not None:
                return JSONResponse(reply)
        result = WebhookReceiver().receive(connector, headers, body)
    except Exception:  # a missing credential or a broken integration: say so to the operator, not to the sender
        with get_db() as conn:
            identity.audit(conn, "webhook.error", actor=f"integration:{integration_id}", outcome="error", workspace_id=row["workspace_id"], target=integration_id)
        return JSONResponse({"status": "error"}, status_code=503)

    if isinstance(result, Rejected):
        if result.reason in ("invalid_signature", "unverified_signature"):
            with get_db() as conn:
                identity.audit(conn, "webhook.rejected", actor=f"integration:{integration_id}", outcome="denied", workspace_id=row["workspace_id"],
                               target=integration_id, detail={"reason": result.reason}, remote=request.client.host if request.client else None)
        if result.reason == "unsupported_event":
            return JSONResponse({"status": "ignored"}, status_code=200)
        return JSONResponse({"status": "rejected", "reason": result.reason}, status_code=_STATUS.get(result.reason, 400))
    if isinstance(result, Duplicate):
        return JSONResponse({"status": "duplicate", "outcome": result.status}, status_code=200)
    assert isinstance(result, Accepted)  # noqa: S101 - the union is exhausted
    event = TriggerEvent.from_envelope(result.envelope)
    receiver = WebhookReceiver()
    try:
        touched: list[str] = await get_engine().deliver_event(event)
    except Exception:
        receiver.record_outcome(event.workspace_id, event.source, event.external_id, "IGNORED")
        with get_db() as conn:
            identity.audit(conn, "webhook.delivery_failed", actor=f"integration:{integration_id}", outcome="error", workspace_id=row["workspace_id"], target=integration_id)
        return JSONResponse({"status": "error"}, status_code=500)
    receiver.record_outcome(event.workspace_id, event.source, event.external_id, "STARTED" if touched else "IGNORED", touched[0] if touched else None)
    out: dict[str, Any] = {"status": "accepted", "workflows": len(touched)}
    return JSONResponse(out, status_code=202)

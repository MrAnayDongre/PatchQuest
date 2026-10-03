"""Demo-only endpoints, registered only when PATCHQUEST_DEMO=1: trigger the scripted GitHub scenario, read what the simulators received."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from patchquest.database import get_db
from patchquest.demo import fixtures as fx
from patchquest.demo import simulators

router = APIRouter(prefix="/api/demo", tags=["demo"])
STATE: dict[str, Any] = {}


def _integration_id(kind: str) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT id FROM integrations WHERE kind = ?", (kind,)).fetchone()
    if row is None:
        raise HTTPException(409, "the demo integrations are not set up yet")
    return str(row["id"])


@router.get("")
async def info() -> dict[str, Any]:
    return {"demo": True, "simulated": ["github", "slack"], "scripted_models": True, "seed": STATE.get("seed", {}).get("runs")}


@router.post("/trigger-issue")
async def trigger_issue(request: Request) -> dict[str, Any]:
    """Open issue #412 in the simulated GitHub and deliver the signed 'labeled agent-ready' webhook to PatchQuest's own /hooks endpoint."""
    sims: simulators.Simulators = STATE["sims"]
    number = sims.open_issue(str(fx.ISSUE["title"]), str(fx.ISSUE["body"]))
    delivery_id = f"demo-{number}-{STATE.setdefault('deliveries', 0)}"
    STATE["deliveries"] += 1
    delivery = sims.labeled_event(number, str(fx.ISSUE["title"]), str(fx.ISSUE["body"]), delivery_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://localhost") as client:
        reply = await client.post(f"/hooks/{_integration_id('github')}", content=delivery.body, headers=delivery.headers)
    return {"issue": number, "webhook_status": reply.status_code, "webhook": reply.json()}


@router.get("/transcript")
async def transcript() -> dict[str, Any]:
    """What the simulated GitHub and Slack servers actually received from PatchQuest."""
    return STATE["sims"].transcript()

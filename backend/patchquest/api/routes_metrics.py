"""Metrics API: aggregates over the runs the caller may read."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from patchquest.api.auth import CurrentPrincipal
from patchquest.config import get_config
from patchquest.database import get_db
from patchquest.domain.identity import Permission
from patchquest.observability.metrics import MetricsQuery, compute, parse_window

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("")
async def metrics(principal: CurrentPrincipal, window: str = Query("7d", description="30m, 24h, 7d, 2w"),
                  group_by: str | None = Query(None, description="model | provider | repository | workspace")) -> dict[str, Any]:
    try:
        query = MetricsQuery(workspace_ids=principal.workspaces_with(Permission.RUN_READ), since=parse_window(window),
                             group_by=group_by, pricing=get_config().pricing)
        with get_db() as conn:
            return compute(conn, query)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

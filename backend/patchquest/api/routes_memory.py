"""Memory API routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query

from patchquest.api.schemas import MemoryRecordResponse
from patchquest.database import get_db

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=list[MemoryRecordResponse])
async def list_memory(
    scope: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, le=500),
) -> list[MemoryRecordResponse]:
    query = "SELECT * FROM memory_records WHERE 1=1"
    params: list[str | int] = []

    if scope:
        query += " AND scope = ?"
        params.append(scope)
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        MemoryRecordResponse(
            id=r["id"],
            scope=r["scope"],
            record_type=r["record_type"],
            key=r["key"],
            value=json.loads(r["value_json"]) if r["value_json"] else None,
            source_path=r["source_path"],
            status=r["status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]

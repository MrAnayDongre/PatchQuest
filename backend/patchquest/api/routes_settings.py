"""Settings API routes."""

from __future__ import annotations

import json

from fastapi import APIRouter

from patchquest.api.schemas import SettingsResponse, UpdateSettingsRequest
from patchquest.database import get_db, now_iso

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    settings = {r["key"]: json.loads(r["value_json"]) for r in rows}
    return SettingsResponse(settings=settings)


@router.post("", response_model=SettingsResponse)
async def update_settings(req: UpdateSettingsRequest) -> SettingsResponse:
    now = now_iso()
    with get_db() as conn:
        for key, value in req.settings.items():
            conn.execute(
                """INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
                (key, json.dumps(value), now),
            )
    return await get_settings()

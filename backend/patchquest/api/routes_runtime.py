"""Runtime status API routes."""

from __future__ import annotations

from fastapi import APIRouter

from patchquest.runtime.runtime_registry import get_runtime_status

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/status")
async def runtime_status() -> dict:
    return get_runtime_status()

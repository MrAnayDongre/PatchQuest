"""Calendar data models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AvailabilityStatus(str, Enum):
    BUSY = "busy"
    TENTATIVE = "tentative"
    FREE = "free"


class CalendarEvent(BaseModel):
    id: str = ""
    calendar_id: str = "patchquest"
    title: str
    description: str = ""
    start_at: str
    end_at: str
    timezone: str = "UTC"
    location: str | None = None
    source_provider: str = "local"
    metadata_json: dict[str, Any] | None = None
    patchquest_run_id: str | None = None
    scheduled_task_id: int | None = None
    reminder_minutes: int | None = None


class AvailabilityBlock(BaseModel):
    start_at: str
    end_at: str
    status: AvailabilityStatus = AvailabilityStatus.BUSY
    source_event_id: str | None = None


class CalendarInfo(BaseModel):
    id: str
    name: str
    provider: str
    color: str | None = None
    writable: bool = True

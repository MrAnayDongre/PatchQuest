"""Calendar API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class CreateEventRequest(BaseModel):
    calendar_id: str = "patchquest"
    title: str
    description: str = ""
    start_at: str
    end_at: str
    timezone: str = "UTC"
    location: str | None = None
    scheduled_task_id: int | None = None
    reminder_minutes: int | None = None


class UpdateEventRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    timezone: str | None = None
    location: str | None = None
    reminder_minutes: int | None = None


class AvailabilityRequest(BaseModel):
    start: str
    end: str
    provider: str | None = None
    calendar_id: str | None = None


class ImportICSRequest(BaseModel):
    ics_text: str | None = None
    file_path: str | None = None


@router.get("/status")
async def calendar_status():
    from patchquest.calendar.calendar_registry import get_provider_status
    from patchquest.config import get_config
    cfg = get_config()
    return {
        "enabled": cfg.calendar.enabled,
        "default_provider": cfg.calendar.default_provider,
        "create_events_for_tasks": cfg.calendar.create_events_for_scheduled_tasks,
        "avoid_busy_times": cfg.calendar.avoid_busy_times,
        "providers": get_provider_status(),
    }


@router.get("/providers")
async def list_providers():
    from patchquest.calendar.calendar_registry import get_provider_status
    return get_provider_status()


@router.get("/events")
async def list_events(start: str, end: str, provider: str | None = None, calendar_id: str | None = None):
    from patchquest.calendar.calendar_service import list_events
    try:
        events = list_events(start, end, provider, calendar_id)
        return [e.model_dump() for e in events]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/events")
async def create_event(req: CreateEventRequest):
    from patchquest.calendar.calendar_models import CalendarEvent
    from patchquest.calendar.calendar_service import create_event
    event = CalendarEvent(
        calendar_id=req.calendar_id, title=req.title, description=req.description,
        start_at=req.start_at, end_at=req.end_at, timezone=req.timezone,
        location=req.location, scheduled_task_id=req.scheduled_task_id,
        reminder_minutes=req.reminder_minutes,
    )
    try:
        created = create_event(event)
        return created.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/events/{event_id}")
async def update_event(event_id: str, req: UpdateEventRequest):
    from patchquest.calendar.calendar_service import list_events as _list, update_event
    from patchquest.calendar.calendar_models import CalendarEvent
    try:
        updates = req.model_dump(exclude_none=True)
        event = CalendarEvent(
            title=updates.get("title", ""),
            start_at=updates.get("start_at", ""),
            end_at=updates.get("end_at", ""),
            **{k: v for k, v in updates.items() if k not in ("title", "start_at", "end_at")},
        )
        updated = update_event(event_id, event)
        return updated.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, provider: str | None = None):
    from patchquest.calendar.calendar_service import delete_event
    ok = delete_event(event_id, provider)
    if not ok:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"deleted": True}


@router.post("/availability")
async def check_availability(req: AvailabilityRequest):
    from patchquest.calendar.calendar_service import get_availability
    blocks = get_availability(req.start, req.end, req.provider, req.calendar_id)
    return [b.model_dump() for b in blocks]


@router.post("/import-ics")
async def import_ics(req: ImportICSRequest):
    from patchquest.calendar.providers.ics_calendar import ICSCalendarProvider
    provider = ICSCalendarProvider()
    try:
        if req.ics_text:
            events = provider.import_from_text(req.ics_text)
        elif req.file_path:
            events = provider.import_from_file(req.file_path)
        else:
            raise HTTPException(status_code=400, detail="Provide ics_text or file_path")
        return {"imported": len(events), "events": [e.model_dump() for e in events]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export-ics")
async def export_ics(start: str | None = None, end: str | None = None):
    from patchquest.calendar.calendar_service import list_events
    from patchquest.calendar.providers.ics_calendar import ICSCalendarProvider
    from datetime import datetime, timedelta, timezone

    s = start or (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    e = end or (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()

    events = list_events(s, e)
    provider = ICSCalendarProvider()
    ics_text = provider.export_events(events)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=ics_text, media_type="text/calendar")


@router.post("/test-provider")
async def test_provider(provider: str):
    from patchquest.calendar.calendar_registry import get_calendar_provider
    try:
        p = get_calendar_provider(provider)
        ok, msg = p.validate_config()
        calendars = p.list_calendars() if ok else []
        return {"provider": provider, "available": ok, "message": msg,
                "calendars": [c.model_dump() for c in calendars]}
    except Exception as e:
        return {"provider": provider, "available": False, "message": str(e), "calendars": []}

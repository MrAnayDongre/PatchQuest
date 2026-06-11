"""Calendar service — provider dispatch, availability checking, scheduler integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from patchquest.calendar.calendar_models import AvailabilityBlock, CalendarEvent
from patchquest.calendar.calendar_registry import get_calendar_provider

logger = logging.getLogger(__name__)


def _default_provider() -> str:
    try:
        from patchquest.config import get_config
        cfg = get_config()
        cal_cfg = getattr(cfg, "calendar", None)
        if cal_cfg:
            return getattr(cal_cfg, "default_provider", "local")
    except Exception:
        pass
    return "local"


def list_events(start: str, end: str, provider_name: str | None = None, calendar_id: str | None = None) -> list[CalendarEvent]:
    provider = get_calendar_provider(provider_name or _default_provider())
    return provider.list_events(start, end, calendar_id)


def create_event(event: CalendarEvent, provider_name: str | None = None) -> CalendarEvent:
    provider = get_calendar_provider(provider_name or _default_provider())
    return provider.create_event(event)


def update_event(event_id: str, event: CalendarEvent, provider_name: str | None = None) -> CalendarEvent:
    provider = get_calendar_provider(provider_name or _default_provider())
    return provider.update_event(event_id, event)


def delete_event(event_id: str, provider_name: str | None = None) -> bool:
    provider = get_calendar_provider(provider_name or _default_provider())
    return provider.delete_event(event_id)


def get_availability(start: str, end: str, provider_name: str | None = None, calendar_id: str | None = None) -> list[AvailabilityBlock]:
    provider = get_calendar_provider(provider_name or _default_provider())
    return provider.get_availability(start, end, calendar_id)


def check_conflicts(start: str, end: str, provider_name: str | None = None) -> list[AvailabilityBlock]:
    blocks = get_availability(start, end, provider_name)
    conflicts = []
    for block in blocks:
        if block.start_at < end and block.end_at > start:
            conflicts.append(block)
    return conflicts


def find_next_available(
    after: str, duration_minutes: int = 60,
    provider_name: str | None = None,
    max_days_ahead: int = 7,
) -> str | None:
    dt = datetime.fromisoformat(after)
    end_search = dt + timedelta(days=max_days_ahead)

    blocks = get_availability(after, end_search.isoformat(), provider_name)
    busy_ranges = [(b.start_at, b.end_at) for b in blocks]
    busy_ranges.sort()

    candidate = dt
    duration = timedelta(minutes=duration_minutes)

    for busy_start_str, busy_end_str in busy_ranges:
        busy_start = datetime.fromisoformat(busy_start_str)
        busy_end = datetime.fromisoformat(busy_end_str)

        if candidate + duration <= busy_start:
            return candidate.isoformat()
        if busy_end > candidate:
            candidate = busy_end

    if candidate + duration <= end_search:
        return candidate.isoformat()
    return None


def create_scheduled_task_event(
    task_id: int,
    title: str,
    start_at: str,
    duration_minutes: int = 30,
    provider_name: str | None = None,
    calendar_id: str | None = None,
    reminder_minutes: int | None = None,
    description: str = "",
    patchquest_run_id: str | None = None,
) -> CalendarEvent:
    dt_start = datetime.fromisoformat(start_at)
    dt_end = dt_start + timedelta(minutes=duration_minutes)

    event = CalendarEvent(
        calendar_id=calendar_id or "patchquest",
        title=f"PatchQuest: {title}",
        description=description or f"Scheduled PatchQuest task #{task_id}",
        start_at=dt_start.isoformat(),
        end_at=dt_end.isoformat(),
        scheduled_task_id=task_id,
        reminder_minutes=reminder_minutes,
        patchquest_run_id=patchquest_run_id,
    )
    return create_event(event, provider_name)


def update_scheduled_task_event(task_id: int, provider_name: str | None = None, **updates) -> CalendarEvent | None:
    provider = get_calendar_provider(provider_name or _default_provider())
    now = datetime.now(timezone.utc)
    events = provider.list_events(
        (now - timedelta(days=365)).isoformat(),
        (now + timedelta(days=365)).isoformat(),
    )
    for ev in events:
        if ev.scheduled_task_id == task_id:
            for k, v in updates.items():
                if hasattr(ev, k):
                    setattr(ev, k, v)
            return provider.update_event(ev.id, ev)
    return None


def delete_scheduled_task_events(task_id: int, provider_name: str | None = None) -> int:
    provider = get_calendar_provider(provider_name or _default_provider())
    now = datetime.now(timezone.utc)
    events = provider.list_events(
        (now - timedelta(days=365)).isoformat(),
        (now + timedelta(days=365)).isoformat(),
    )
    count = 0
    for ev in events:
        if ev.scheduled_task_id == task_id:
            if provider.delete_event(ev.id):
                count += 1
    return count

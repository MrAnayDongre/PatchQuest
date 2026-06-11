"""Tests for local SQLite-backed calendar provider."""

import pytest

from patchquest.calendar.calendar_models import CalendarEvent
from patchquest.calendar.providers.local_calendar import LocalCalendarProvider
from patchquest.database import init_db, set_db_path

import tempfile
from pathlib import Path


@pytest.fixture
def local_cal(tmp_path):
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    init_db()
    return LocalCalendarProvider()


def test_list_calendars(local_cal):
    cals = local_cal.list_calendars()
    assert len(cals) == 1
    assert cals[0].id == "patchquest"


def test_create_and_list_events(local_cal):
    event = CalendarEvent(
        title="Test Event",
        start_at="2026-06-15T09:00:00+00:00",
        end_at="2026-06-15T10:00:00+00:00",
    )
    created = local_cal.create_event(event)
    assert created.id

    events = local_cal.list_events("2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z")
    assert len(events) == 1
    assert events[0].title == "Test Event"


def test_update_event(local_cal):
    event = CalendarEvent(
        title="Original",
        start_at="2026-06-15T09:00:00+00:00",
        end_at="2026-06-15T10:00:00+00:00",
    )
    created = local_cal.create_event(event)

    updated_event = CalendarEvent(
        title="Updated",
        start_at="2026-06-15T11:00:00+00:00",
        end_at="2026-06-15T12:00:00+00:00",
    )
    updated = local_cal.update_event(created.id, updated_event)
    assert updated.title == "Updated"


def test_delete_event(local_cal):
    event = CalendarEvent(
        title="To Delete",
        start_at="2026-06-15T09:00:00+00:00",
        end_at="2026-06-15T10:00:00+00:00",
    )
    created = local_cal.create_event(event)
    assert local_cal.delete_event(created.id)

    events = local_cal.list_events("2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z")
    assert len(events) == 0


def test_delete_nonexistent_returns_false(local_cal):
    assert local_cal.delete_event("nonexistent") is False


def test_availability(local_cal):
    local_cal.create_event(CalendarEvent(
        title="Busy",
        start_at="2026-06-15T09:00:00+00:00",
        end_at="2026-06-15T10:00:00+00:00",
    ))
    blocks = local_cal.get_availability("2026-06-15T00:00:00Z", "2026-06-16T00:00:00Z")
    assert len(blocks) == 1
    assert blocks[0].source_event_id is not None


def test_event_with_scheduled_task_id(local_cal):
    event = CalendarEvent(
        title="Scheduled",
        start_at="2026-06-15T09:00:00+00:00",
        end_at="2026-06-15T10:00:00+00:00",
        scheduled_task_id=42,
        reminder_minutes=10,
    )
    created = local_cal.create_event(event)
    events = local_cal.list_events("2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z")
    assert events[0].scheduled_task_id == 42
    assert events[0].reminder_minutes == 10

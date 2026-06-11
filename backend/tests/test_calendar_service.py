"""Tests for calendar service — conflict detection, scheduler integration."""

import pytest

from patchquest.calendar.calendar_models import CalendarEvent
from patchquest.calendar.calendar_service import (
    check_conflicts,
    create_event,
    create_scheduled_task_event,
    delete_event,
    find_next_available,
    get_availability,
    list_events,
)
from patchquest.database import init_db, set_db_path


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    init_db()


def test_create_and_list():
    ev = create_event(CalendarEvent(
        title="Test", start_at="2026-06-15T09:00:00+00:00", end_at="2026-06-15T10:00:00+00:00",
    ))
    events = list_events("2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z")
    assert len(events) == 1


def test_conflict_detection():
    create_event(CalendarEvent(
        title="Meeting", start_at="2026-06-15T09:00:00+00:00", end_at="2026-06-15T10:00:00+00:00",
    ))
    conflicts = check_conflicts("2026-06-15T09:30:00+00:00", "2026-06-15T10:30:00+00:00")
    assert len(conflicts) >= 1


def test_no_conflict_when_not_overlapping():
    create_event(CalendarEvent(
        title="Meeting", start_at="2026-06-15T09:00:00+00:00", end_at="2026-06-15T10:00:00+00:00",
    ))
    conflicts = check_conflicts("2026-06-15T11:00:00+00:00", "2026-06-15T12:00:00+00:00")
    assert len(conflicts) == 0


def test_scheduled_task_event_creation():
    ev = create_scheduled_task_event(
        task_id=1, title="Daily Tests",
        start_at="2026-06-15T09:00:00+00:00",
        duration_minutes=30,
        reminder_minutes=10,
    )
    assert ev.scheduled_task_id == 1
    assert "PatchQuest: Daily Tests" in ev.title
    assert ev.reminder_minutes == 10


def test_find_next_available_slot():
    create_event(CalendarEvent(
        title="Busy", start_at="2026-06-15T09:00:00+00:00", end_at="2026-06-15T10:00:00+00:00",
    ))
    slot = find_next_available("2026-06-15T09:00:00+00:00", duration_minutes=30)
    assert slot is not None
    assert slot >= "2026-06-15T10:00:00"

"""Tests for calendar data models."""

from patchquest.calendar.calendar_models import (
    AvailabilityBlock,
    AvailabilityStatus,
    CalendarEvent,
    CalendarInfo,
)


def test_calendar_event_defaults():
    ev = CalendarEvent(title="Test", start_at="2026-01-01T09:00:00Z", end_at="2026-01-01T10:00:00Z")
    assert ev.calendar_id == "patchquest"
    assert ev.timezone == "UTC"
    assert ev.source_provider == "local"
    assert ev.scheduled_task_id is None


def test_availability_block():
    block = AvailabilityBlock(
        start_at="2026-01-01T09:00:00Z",
        end_at="2026-01-01T10:00:00Z",
    )
    assert block.status == AvailabilityStatus.BUSY


def test_calendar_info():
    info = CalendarInfo(id="test", name="Test Cal", provider="local")
    assert info.writable is True

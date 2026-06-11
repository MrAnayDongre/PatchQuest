"""Tests for ICS calendar provider."""

import pytest

from patchquest.calendar.calendar_models import CalendarEvent
from patchquest.calendar.providers.ics_calendar import ICSCalendarProvider


@pytest.fixture
def ics_provider(tmp_path):
    return ICSCalendarProvider(export_path=str(tmp_path / "test.ics"))


def test_export_events(ics_provider):
    events = [
        CalendarEvent(
            id="ev1", title="Test Event",
            start_at="2026-06-15T09:00:00+00:00",
            end_at="2026-06-15T10:00:00+00:00",
        ),
        CalendarEvent(
            id="ev2", title="Event 2",
            start_at="2026-06-16T14:00:00+00:00",
            end_at="2026-06-16T15:00:00+00:00",
            description="Description here",
            reminder_minutes=15,
        ),
    ]
    ics_text = ics_provider.export_events(events)
    assert "BEGIN:VCALENDAR" in ics_text
    assert "END:VCALENDAR" in ics_text
    assert "Test Event" in ics_text
    assert "Event 2" in ics_text
    assert "VALARM" in ics_text


def test_export_and_reimport(ics_provider):
    events = [
        CalendarEvent(
            id="round-trip", title="Round Trip",
            start_at="2026-06-15T09:00:00+00:00",
            end_at="2026-06-15T10:00:00+00:00",
            location="Office",
        ),
    ]
    ics_text = ics_provider.export_events(events)
    reimported = ics_provider.import_from_text(ics_text)
    assert len(reimported) == 1
    assert reimported[0].title == "Round Trip"
    assert reimported[0].location == "Office"
    assert reimported[0].id == "round-trip"


def test_export_to_file(ics_provider):
    events = [
        CalendarEvent(
            id="file-test", title="File Test",
            start_at="2026-06-15T09:00:00+00:00",
            end_at="2026-06-15T10:00:00+00:00",
        ),
    ]
    path = ics_provider.export_to_file(events)
    from pathlib import Path
    assert Path(path).exists()
    content = Path(path).read_text()
    assert "File Test" in content


def test_create_event_appends(ics_provider):
    ev1 = CalendarEvent(
        title="First", start_at="2026-06-15T09:00:00+00:00", end_at="2026-06-15T10:00:00+00:00",
    )
    ev2 = CalendarEvent(
        title="Second", start_at="2026-06-16T09:00:00+00:00", end_at="2026-06-16T10:00:00+00:00",
    )
    ics_provider.create_event(ev1)
    ics_provider.create_event(ev2)

    events = ics_provider.list_events("2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z")
    assert len(events) == 2


def test_parse_empty_file_returns_empty(ics_provider):
    events = ics_provider.list_events("2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")
    assert events == []

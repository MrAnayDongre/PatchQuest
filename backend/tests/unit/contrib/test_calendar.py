"""Calendar models, service and the local/ICS providers (contrib, outside the core coding-agent path).
"""

import pytest

from patchquest.calendar.calendar_models import (
    AvailabilityBlock,
    AvailabilityStatus,
    CalendarEvent,
    CalendarInfo,
)
from patchquest.calendar.calendar_service import (
    check_conflicts,
    create_event,
    create_scheduled_task_event,
    find_next_available,
    list_events,
)
from patchquest.calendar.providers.ics_calendar import ICSCalendarProvider
from patchquest.calendar.providers.local_calendar import LocalCalendarProvider
from patchquest.database import init_db, set_db_path

# ======================================================================
# Models
# ======================================================================

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


# ======================================================================
# Service
# ======================================================================

def test_create_and_list():
    create_event(CalendarEvent(
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


# ======================================================================
# Local provider
# ======================================================================

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
    local_cal.create_event(event)
    events = local_cal.list_events("2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z")
    assert events[0].scheduled_task_id == 42
    assert events[0].reminder_minutes == 10


# ======================================================================
# ICS export
# ======================================================================

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

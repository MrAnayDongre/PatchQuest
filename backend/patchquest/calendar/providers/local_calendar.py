"""SQLite-backed local calendar provider."""

from __future__ import annotations

import uuid

from patchquest.calendar.calendar_models import AvailabilityBlock, CalendarEvent, CalendarInfo
from patchquest.calendar.calendar_provider_base import CalendarProvider
from patchquest.database import get_db, now_iso


class LocalCalendarProvider(CalendarProvider):
    name = "local"

    def list_calendars(self) -> list[CalendarInfo]:
        return [CalendarInfo(id="patchquest", name="PatchQuest", provider=self.name)]

    def list_events(self, start: str, end: str, calendar_id: str | None = None) -> list[CalendarEvent]:
        cal_id = calendar_id or "patchquest"
        with get_db() as conn:
            rows = conn.execute(
                """SELECT * FROM calendar_events
                   WHERE calendar_id = ? AND end_at >= ? AND start_at <= ?
                   ORDER BY start_at""",
                (cal_id, start, end),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        event.id = event.id or str(uuid.uuid4())
        event.source_provider = self.name
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO calendar_events
                   (id, calendar_id, title, description, start_at, end_at, timezone,
                    location, source_provider, metadata_json, patchquest_run_id,
                    scheduled_task_id, reminder_minutes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.id, event.calendar_id, event.title, event.description,
                 event.start_at, event.end_at, event.timezone, event.location,
                 event.source_provider, _json_dumps(event.metadata_json),
                 event.patchquest_run_id, event.scheduled_task_id,
                 event.reminder_minutes, now, now),
            )
        return event

    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEvent:
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """UPDATE calendar_events SET
                   title=?, description=?, start_at=?, end_at=?, timezone=?,
                   location=?, metadata_json=?, patchquest_run_id=?,
                   scheduled_task_id=?, reminder_minutes=?, updated_at=?
                   WHERE id=?""",
                (event.title, event.description, event.start_at, event.end_at,
                 event.timezone, event.location, _json_dumps(event.metadata_json),
                 event.patchquest_run_id, event.scheduled_task_id,
                 event.reminder_minutes, now, event_id),
            )
        event.id = event_id
        return event

    def delete_event(self, event_id: str) -> bool:
        with get_db() as conn:
            cursor = conn.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
            return cursor.rowcount > 0

    def get_availability(self, start: str, end: str, calendar_id: str | None = None) -> list[AvailabilityBlock]:
        events = self.list_events(start, end, calendar_id)
        return [
            AvailabilityBlock(start_at=e.start_at, end_at=e.end_at, source_event_id=e.id)
            for e in events
        ]

    def _row_to_event(self, row) -> CalendarEvent:
        import json
        meta = None
        if row["metadata_json"]:
            try:
                meta = json.loads(row["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        return CalendarEvent(
            id=row["id"], calendar_id=row["calendar_id"], title=row["title"],
            description=row["description"] or "", start_at=row["start_at"],
            end_at=row["end_at"], timezone=row["timezone"] or "UTC",
            location=row["location"], source_provider=row["source_provider"],
            metadata_json=meta, patchquest_run_id=row["patchquest_run_id"],
            scheduled_task_id=row["scheduled_task_id"],
            reminder_minutes=row["reminder_minutes"],
        )


def _json_dumps(obj) -> str | None:
    if obj is None:
        return None
    import json
    return json.dumps(obj)

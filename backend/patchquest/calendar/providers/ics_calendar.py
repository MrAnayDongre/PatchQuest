"""ICS file import/export calendar provider."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from patchquest.calendar.calendar_models import CalendarEvent, CalendarInfo
from patchquest.calendar.calendar_provider_base import CalendarProvider


class ICSCalendarProvider(CalendarProvider):
    name = "ics"

    def __init__(self, export_path: str = ".patchquest/calendar/patchquest.ics"):
        self.export_path = Path(export_path)

    def validate_config(self) -> tuple[bool, str]:
        return True, "ok"

    def list_calendars(self) -> list[CalendarInfo]:
        return [CalendarInfo(id="ics-export", name="ICS Export", provider=self.name, writable=False)]

    def list_events(self, start: str, end: str, calendar_id: str | None = None) -> list[CalendarEvent]:
        if not self.export_path.exists():
            return []
        return self._parse_ics(self.export_path.read_text(), start, end)

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        event.id = event.id or str(uuid.uuid4())
        event.source_provider = self.name
        self._append_to_ics(event)
        return event

    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEvent:
        raise NotImplementedError("ICS provider is append-only; use local provider for edits")

    def delete_event(self, event_id: str) -> bool:
        raise NotImplementedError("ICS provider is append-only; use local provider for deletes")

    def export_events(self, events: list[CalendarEvent]) -> str:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//PatchQuest//EN",
            "CALSCALE:GREGORIAN",
        ]
        for event in events:
            lines.extend(self._event_to_vevent(event))
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines)

    def export_to_file(self, events: list[CalendarEvent], path: str | None = None) -> str:
        target = Path(path) if path else self.export_path
        target.parent.mkdir(parents=True, exist_ok=True)
        content = self.export_events(events)
        target.write_text(content)
        return str(target)

    def import_from_file(self, path: str) -> list[CalendarEvent]:
        content = Path(path).read_text()
        return self._parse_ics(content)

    def import_from_text(self, ics_text: str) -> list[CalendarEvent]:
        return self._parse_ics(ics_text)

    def _event_to_vevent(self, event: CalendarEvent) -> list[str]:
        uid = event.id or str(uuid.uuid4())
        dtstart = _to_ical_dt(event.start_at)
        dtend = _to_ical_dt(event.end_at)
        lines = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{_escape_ical(event.title)}",
        ]
        if event.description:
            lines.append(f"DESCRIPTION:{_escape_ical(event.description)}")
        if event.location:
            lines.append(f"LOCATION:{_escape_ical(event.location)}")
        if event.reminder_minutes:
            lines.extend([
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"TRIGGER:-PT{event.reminder_minutes}M",
                "DESCRIPTION:Reminder",
                "END:VALARM",
            ])
        lines.append("END:VEVENT")
        return lines

    def _append_to_ics(self, event: CalendarEvent) -> None:
        self.export_path.parent.mkdir(parents=True, exist_ok=True)
        if self.export_path.exists():
            content = self.export_path.read_text()
            if "END:VCALENDAR" in content:
                vevent = "\r\n".join(self._event_to_vevent(event))
                content = content.replace("END:VCALENDAR", vevent + "\r\nEND:VCALENDAR")
                self.export_path.write_text(content)
                return
        self.export_to_file([event])

    def _parse_ics(self, content: str, start: str | None = None, end: str | None = None) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        in_event = False
        current: dict = {}
        for line in content.replace("\r\n", "\n").split("\n"):
            line = line.strip()
            if line == "BEGIN:VEVENT":
                in_event = True
                current = {}
            elif line == "END:VEVENT" and in_event:
                in_event = False
                ev = self._dict_to_event(current)
                if ev:
                    if start and ev.end_at < start:
                        continue
                    if end and ev.start_at > end:
                        continue
                    events.append(ev)
            elif in_event and ":" in line:
                key, _, value = line.partition(":")
                key = key.split(";")[0]
                current[key] = value
        return events

    def _dict_to_event(self, d: dict) -> CalendarEvent | None:
        if "DTSTART" not in d or "DTEND" not in d:
            return None
        return CalendarEvent(
            id=d.get("UID", str(uuid.uuid4())),
            title=_unescape_ical(d.get("SUMMARY", "Untitled")),
            description=_unescape_ical(d.get("DESCRIPTION", "")),
            start_at=_from_ical_dt(d["DTSTART"]),
            end_at=_from_ical_dt(d["DTEND"]),
            location=_unescape_ical(d.get("LOCATION", "")) or None,
            source_provider=self.name,
        )


def _to_ical_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        return iso.replace("-", "").replace(":", "").replace(".", "")[:15] + "Z"


def _from_ical_dt(ical: str) -> str:
    try:
        ical = ical.rstrip("Z")
        dt = datetime.strptime(ical[:15], "%Y%m%dT%H%M%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return ical


def _escape_ical(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _unescape_ical(text: str) -> str:
    return text.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")

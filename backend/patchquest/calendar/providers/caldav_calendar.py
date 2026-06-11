"""CalDAV calendar provider.

Requires the `caldav` Python package (pip install caldav).
Supports any CalDAV-compatible server (Nextcloud, Radicale, Baikal, etc.).
"""

from __future__ import annotations

import logging
import uuid

from patchquest.calendar.calendar_models import CalendarEvent, CalendarInfo
from patchquest.calendar.calendar_provider_base import CalendarProvider

logger = logging.getLogger(__name__)


class CalDAVCalendarProvider(CalendarProvider):
    name = "caldav"

    def __init__(
        self,
        url_env: str = "CALDAV_URL",
        username_env: str = "CALDAV_USERNAME",
        password_env: str = "CALDAV_PASSWORD",
    ):
        self.url_env = url_env
        self.username_env = username_env
        self.password_env = password_env

    def validate_config(self) -> tuple[bool, str]:
        try:
            import caldav  # noqa: F401
        except ImportError:
            return False, "caldav package not installed (pip install caldav)"
        url = self._get_env(self.url_env)
        if not url:
            return False, f"Missing env var {self.url_env}"
        return True, "ok"

    def _client(self):
        import caldav
        url = self._get_env(self.url_env) or ""
        username = self._get_env(self.username_env)
        password = self._get_env(self.password_env)
        return caldav.DAVClient(url=url, username=username, password=password)

    def list_calendars(self) -> list[CalendarInfo]:
        client = self._client()
        principal = client.principal()
        cals = principal.calendars()
        return [
            CalendarInfo(id=str(c.url), name=c.name or str(c.url), provider=self.name)
            for c in cals
        ]

    def list_events(self, start: str, end: str, calendar_id: str | None = None) -> list[CalendarEvent]:
        from datetime import datetime
        client = self._client()
        principal = client.principal()
        cals = principal.calendars()
        if calendar_id:
            cals = [c for c in cals if str(c.url) == calendar_id]
        if not cals:
            return []

        dt_start = datetime.fromisoformat(start)
        dt_end = datetime.fromisoformat(end)
        events = []
        for cal in cals:
            for ev in cal.date_search(dt_start, dt_end):
                parsed = self._parse_vevent(ev.data, str(cal.url))
                if parsed:
                    events.append(parsed)
        return events

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        from patchquest.calendar.providers.ics_calendar import ICSCalendarProvider
        ics_prov = ICSCalendarProvider()
        vevent_lines = ics_prov._event_to_vevent(event)
        ics_text = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//PatchQuest//EN"] + vevent_lines + ["END:VCALENDAR"])

        client = self._client()
        principal = client.principal()
        cals = principal.calendars()
        target = cals[0] if cals else None
        if event.calendar_id:
            for c in cals:
                if str(c.url) == event.calendar_id:
                    target = c
                    break
        if not target:
            raise ValueError("No calendar found")
        target.save_event(ics_text)
        event.id = event.id or str(uuid.uuid4())
        return event

    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEvent:
        self.delete_event(event_id)
        event.id = event_id
        return self.create_event(event)

    def delete_event(self, event_id: str) -> bool:
        client = self._client()
        principal = client.principal()
        for cal in principal.calendars():
            try:
                ev = cal.event_by_uid(event_id)
                ev.delete()
                return True
            except Exception:
                continue
        return False

    def _parse_vevent(self, ics_data: str, calendar_id: str) -> CalendarEvent | None:
        from patchquest.calendar.providers.ics_calendar import ICSCalendarProvider
        ics_prov = ICSCalendarProvider()
        events = ics_prov._parse_ics(ics_data)
        if events:
            ev = events[0]
            ev.calendar_id = calendar_id
            ev.source_provider = self.name
            return ev
        return None

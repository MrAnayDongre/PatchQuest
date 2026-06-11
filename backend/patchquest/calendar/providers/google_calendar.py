"""Google Calendar provider — provider-ready, requires OAuth credentials.

This provider is structurally complete but requires Google OAuth credentials
to function. Users must set up a Google Cloud project with Calendar API enabled
and provide credentials via the GOOGLE_CALENDAR_CREDENTIALS environment variable.

Functional when configured; clearly reports not-configured otherwise.
"""

from __future__ import annotations

import logging

from patchquest.calendar.calendar_models import CalendarEvent, CalendarInfo
from patchquest.calendar.calendar_provider_base import CalendarProvider

logger = logging.getLogger(__name__)


class GoogleCalendarProvider(CalendarProvider):
    name = "google"

    def __init__(self, credentials_env: str = "GOOGLE_CALENDAR_CREDENTIALS"):
        self.credentials_env = credentials_env

    def validate_config(self) -> tuple[bool, str]:
        creds = self._get_env(self.credentials_env)
        if not creds:
            return False, (
                f"Not configured: set {self.credentials_env} to the path of your "
                "Google OAuth credentials JSON file. Requires a Google Cloud project "
                "with Calendar API enabled."
            )
        try:
            self._get_service()
            return True, "ok"
        except Exception as e:
            return False, f"Configuration error: {e}"

    def _get_service(self):
        import json
        import os
        from pathlib import Path

        creds_path = os.environ.get(self.credentials_env, "")
        if not creds_path or not Path(creds_path).exists():
            raise ValueError(f"Credentials file not found at {creds_path}")

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            raise ImportError(
                "google-api-python-client and google-auth packages required. "
                "Install with: pip install google-api-python-client google-auth-oauthlib"
            )

        creds_data = json.loads(Path(creds_path).read_text())
        credentials = Credentials.from_authorized_user_info(creds_data)
        return build("calendar", "v3", credentials=credentials)

    def list_calendars(self) -> list[CalendarInfo]:
        service = self._get_service()
        result = service.calendarList().list().execute()
        return [
            CalendarInfo(
                id=cal["id"], name=cal.get("summary", cal["id"]),
                provider=self.name, color=cal.get("backgroundColor"),
            )
            for cal in result.get("items", [])
        ]

    def list_events(self, start: str, end: str, calendar_id: str | None = None) -> list[CalendarEvent]:
        service = self._get_service()
        cal_id = calendar_id or "primary"
        result = service.events().list(
            calendarId=cal_id, timeMin=start, timeMax=end,
            singleEvents=True, orderBy="startTime",
        ).execute()
        events = []
        for item in result.get("items", []):
            events.append(CalendarEvent(
                id=item["id"],
                calendar_id=cal_id,
                title=item.get("summary", ""),
                description=item.get("description", ""),
                start_at=item.get("start", {}).get("dateTime", item.get("start", {}).get("date", "")),
                end_at=item.get("end", {}).get("dateTime", item.get("end", {}).get("date", "")),
                timezone=item.get("start", {}).get("timeZone", "UTC"),
                location=item.get("location"),
                source_provider=self.name,
            ))
        return events

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        service = self._get_service()
        body = {
            "summary": event.title,
            "description": event.description,
            "start": {"dateTime": event.start_at, "timeZone": event.timezone},
            "end": {"dateTime": event.end_at, "timeZone": event.timezone},
        }
        if event.location:
            body["location"] = event.location
        result = service.events().insert(calendarId=event.calendar_id or "primary", body=body).execute()
        event.id = result["id"]
        return event

    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEvent:
        service = self._get_service()
        body = {
            "summary": event.title,
            "description": event.description,
            "start": {"dateTime": event.start_at, "timeZone": event.timezone},
            "end": {"dateTime": event.end_at, "timeZone": event.timezone},
        }
        if event.location:
            body["location"] = event.location
        service.events().update(
            calendarId=event.calendar_id or "primary", eventId=event_id, body=body,
        ).execute()
        event.id = event_id
        return event

    def delete_event(self, event_id: str) -> bool:
        service = self._get_service()
        try:
            service.events().delete(calendarId="primary", eventId=event_id).execute()
            return True
        except Exception:
            return False

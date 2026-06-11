"""Microsoft Graph (Outlook) calendar provider — provider-ready.

Requires Azure AD app registration with Calendar.ReadWrite permissions.
Set MICROSOFT_CLIENT_ID and MICROSOFT_TENANT_ID environment variables.

Functional when configured with proper OAuth tokens; clearly reports
not-configured otherwise.
"""

from __future__ import annotations

import logging

from patchquest.calendar.calendar_models import CalendarEvent, CalendarInfo
from patchquest.calendar.calendar_provider_base import CalendarProvider

logger = logging.getLogger(__name__)


class MicrosoftGraphCalendarProvider(CalendarProvider):
    name = "microsoft"

    def __init__(
        self,
        client_id_env: str = "MICROSOFT_CLIENT_ID",
        tenant_id_env: str = "MICROSOFT_TENANT_ID",
        token_env: str = "MICROSOFT_GRAPH_TOKEN",
    ):
        self.client_id_env = client_id_env
        self.tenant_id_env = tenant_id_env
        self.token_env = token_env

    def validate_config(self) -> tuple[bool, str]:
        client_id = self._get_env(self.client_id_env)
        if not client_id:
            return False, (
                f"Not configured: set {self.client_id_env} and {self.tenant_id_env}. "
                "Requires Azure AD app registration with Calendar.ReadWrite permissions."
            )
        token = self._get_env(self.token_env)
        if not token:
            return False, (
                f"Missing {self.token_env}. Provide a valid Microsoft Graph access token."
            )
        return True, "ok"

    def _headers(self) -> dict[str, str]:
        token = self._get_env(self.token_env) or ""
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def list_calendars(self) -> list[CalendarInfo]:
        import httpx
        resp = httpx.get(
            "https://graph.microsoft.com/v1.0/me/calendars",
            headers=self._headers(), timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            CalendarInfo(
                id=cal["id"], name=cal.get("name", ""),
                provider=self.name, color=cal.get("hexColor"),
            )
            for cal in data.get("value", [])
        ]

    def list_events(self, start: str, end: str, calendar_id: str | None = None) -> list[CalendarEvent]:
        import httpx
        base = "https://graph.microsoft.com/v1.0/me"
        if calendar_id:
            url = f"{base}/calendars/{calendar_id}/events"
        else:
            url = f"{base}/calendar/events"

        resp = httpx.get(
            url, headers=self._headers(), timeout=15,
            params={"$filter": f"start/dateTime ge '{start}' and end/dateTime le '{end}'",
                    "$orderby": "start/dateTime", "$top": "50"},
        )
        resp.raise_for_status()
        data = resp.json()
        events = []
        for item in data.get("value", []):
            events.append(CalendarEvent(
                id=item["id"],
                calendar_id=calendar_id or "default",
                title=item.get("subject", ""),
                description=item.get("bodyPreview", ""),
                start_at=item.get("start", {}).get("dateTime", ""),
                end_at=item.get("end", {}).get("dateTime", ""),
                timezone=item.get("start", {}).get("timeZone", "UTC"),
                location=item.get("location", {}).get("displayName"),
                source_provider=self.name,
            ))
        return events

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        import httpx
        body = {
            "subject": event.title,
            "body": {"contentType": "text", "content": event.description},
            "start": {"dateTime": event.start_at, "timeZone": event.timezone},
            "end": {"dateTime": event.end_at, "timeZone": event.timezone},
        }
        if event.location:
            body["location"] = {"displayName": event.location}
        resp = httpx.post(
            "https://graph.microsoft.com/v1.0/me/calendar/events",
            headers=self._headers(), json=body, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        event.id = data["id"]
        return event

    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEvent:
        import httpx
        body = {
            "subject": event.title,
            "body": {"contentType": "text", "content": event.description},
            "start": {"dateTime": event.start_at, "timeZone": event.timezone},
            "end": {"dateTime": event.end_at, "timeZone": event.timezone},
        }
        if event.location:
            body["location"] = {"displayName": event.location}
        httpx.patch(
            f"https://graph.microsoft.com/v1.0/me/events/{event_id}",
            headers=self._headers(), json=body, timeout=15,
        ).raise_for_status()
        event.id = event_id
        return event

    def delete_event(self, event_id: str) -> bool:
        import httpx
        try:
            httpx.delete(
                f"https://graph.microsoft.com/v1.0/me/events/{event_id}",
                headers=self._headers(), timeout=15,
            ).raise_for_status()
            return True
        except Exception:
            return False

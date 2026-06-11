"""Abstract base class for calendar providers."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from patchquest.calendar.calendar_models import AvailabilityBlock, CalendarEvent, CalendarInfo


class CalendarProvider(ABC):
    name: str = "base"

    @abstractmethod
    def list_calendars(self) -> list[CalendarInfo]:
        ...

    @abstractmethod
    def list_events(self, start: str, end: str, calendar_id: str | None = None) -> list[CalendarEvent]:
        ...

    @abstractmethod
    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        ...

    @abstractmethod
    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEvent:
        ...

    @abstractmethod
    def delete_event(self, event_id: str) -> bool:
        ...

    def get_availability(self, start: str, end: str, calendar_id: str | None = None) -> list[AvailabilityBlock]:
        events = self.list_events(start, end, calendar_id)
        return [
            AvailabilityBlock(start_at=e.start_at, end_at=e.end_at, source_event_id=e.id)
            for e in events
        ]

    def validate_config(self) -> tuple[bool, str]:
        return True, "ok"

    def _get_env(self, env_var: str | None) -> str | None:
        if not env_var:
            return None
        return os.environ.get(env_var)

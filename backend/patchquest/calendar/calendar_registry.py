"""Calendar provider registry."""

from __future__ import annotations

import logging
from typing import Type

from patchquest.calendar.calendar_provider_base import CalendarProvider

logger = logging.getLogger(__name__)

_providers: dict[str, Type[CalendarProvider]] = {}


def register_calendar_provider(name: str, cls: Type[CalendarProvider]) -> None:
    _providers[name] = cls


def get_calendar_provider(name: str, **kwargs) -> CalendarProvider:
    cls = _providers.get(name)
    if cls is None:
        raise ValueError(f"Unknown calendar provider: {name}. Available: {list(_providers.keys())}")
    return cls(**kwargs)


def list_calendar_providers() -> list[str]:
    return list(_providers.keys())


def get_provider_status() -> list[dict]:
    statuses = []
    for name, cls in _providers.items():
        try:
            instance = cls()
            ok, msg = instance.validate_config()
            statuses.append({"name": name, "available": ok, "message": msg})
        except Exception as e:
            statuses.append({"name": name, "available": False, "message": str(e)})
    return statuses


def _auto_register() -> None:
    from patchquest.calendar.providers.local_calendar import LocalCalendarProvider
    from patchquest.calendar.providers.ics_calendar import ICSCalendarProvider
    from patchquest.calendar.providers.caldav_calendar import CalDAVCalendarProvider
    from patchquest.calendar.providers.google_calendar import GoogleCalendarProvider
    from patchquest.calendar.providers.microsoft_graph_calendar import MicrosoftGraphCalendarProvider

    register_calendar_provider("local", LocalCalendarProvider)
    register_calendar_provider("ics", ICSCalendarProvider)
    register_calendar_provider("caldav", CalDAVCalendarProvider)
    register_calendar_provider("google", GoogleCalendarProvider)
    register_calendar_provider("microsoft", MicrosoftGraphCalendarProvider)


_auto_register()

"""Injectable time, so retry schedules, replay windows and grant expiry are testable without sleeping."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso(moment: datetime) -> str:
    """Fixed-width UTC text: lexicographic order equals chronological order, so SQL can compare it."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

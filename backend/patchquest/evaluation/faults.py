"""Fault injection for the evaluation lab and tests: make a run "die" at an exact point.

``SimulatedCrash`` is a ``BaseException`` so no handler in the runtime swallows it, exactly like a process death
between two instructions. ``crash_when`` raises it right after the first event matching a predicate has been
*committed*, so the database is left precisely as a real crash at that moment would leave it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from patchquest.orchestrator.event_bus import event_bus


class SimulatedCrash(BaseException):
    """Stands in for the process dying."""


@contextmanager
def crash_when(predicate: Callable[[dict], bool]) -> Iterator[None]:
    real = event_bus.emit

    async def emit(run_id: str, event: dict) -> None:
        await real(run_id, event)
        if predicate(event):
            raise SimulatedCrash(event["type"])

    event_bus.emit = emit  # type: ignore[method-assign]
    try:
        yield
    finally:
        del event_bus.emit  # drops the instance override, restoring the class method


def after_event(event_type: str, **match: object) -> Callable[[dict], bool]:
    return lambda e: e["type"] == event_type and all(e.get(k) == v for k, v in match.items())

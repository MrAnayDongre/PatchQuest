"""Run lifecycle: the only legal ways a run's status may change.

Every status change goes through ``persistence.runs.transition``, which checks it against
``TRANSITIONS`` and records who made it and why. Nothing else may write ``runs.status``.
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    CANCEL_REQUESTED = "cancel_requested"
    INTERRUPTED = "interrupted"  # the process died; resumable
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_S = RunStatus

TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    _S.CREATED: frozenset({_S.RUNNING, _S.CANCELLED, _S.FAILED}),
    _S.RUNNING: frozenset({_S.WAITING_APPROVAL, _S.CANCEL_REQUESTED, _S.COMPLETED, _S.FAILED, _S.CANCELLED, _S.INTERRUPTED}),
    _S.WAITING_APPROVAL: frozenset({_S.RUNNING, _S.CANCEL_REQUESTED, _S.CANCELLED, _S.FAILED, _S.INTERRUPTED}),
    # A cancel can lose the race against the last phase: finishing is then the truthful outcome.
    _S.CANCEL_REQUESTED: frozenset({_S.CANCELLED, _S.COMPLETED, _S.FAILED, _S.INTERRUPTED}),
    _S.INTERRUPTED: frozenset({_S.RUNNING, _S.CANCELLED, _S.FAILED}),
    _S.COMPLETED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELLED: frozenset(),
}

TERMINAL: frozenset[RunStatus] = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)
ACTIVE: frozenset[RunStatus] = frozenset({_S.RUNNING, _S.WAITING_APPROVAL, _S.CANCEL_REQUESTED})


class IllegalTransition(RuntimeError):
    def __init__(self, run_id: str, current: str, target: str) -> None:
        super().__init__(f"run {run_id}: illegal status change {current} -> {target}")
        self.run_id, self.current, self.target = run_id, current, target


class StaleTransition(RuntimeError):
    """Another writer changed the run between the read and the compare-and-set."""


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    return target in TRANSITIONS[current]

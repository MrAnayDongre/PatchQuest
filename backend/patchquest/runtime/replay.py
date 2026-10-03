"""Replay: understand a past run without repeating its side effects.

* ``replay_state``  - rebuild what happened from the ledger alone and check it is self-consistent.
* model replay      - a child run whose model answers are the recorded ones (see ``providers_recorded``).
* live replay       - a child run that asks the real model again.

Model and live replays never promote to the repository (``promote_policy=never``): they work in the
shadow workspace and leave a diff. ``compare_runs`` says where a replay and its original differ.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from patchquest.database import get_db
from patchquest.domain.runs import RunStatus, can_transition
from patchquest.persistence import checkpoints, ledger

# Events that mark the shape of a run. Timing, ids and wording are deliberately not part of the comparison.
_MILESTONES = frozenset({"phase_started", "phase_skipped", "patch_staged", "patch_rejected", "repair_started",
                         "tests_completed", "secret_detected", "plan_scope_overridden", "patch_empty", "patch_retry"})
REPLAY_OVERRIDES = {"agent.promote_policy": "never"}


class ReplayMode(StrEnum):
    STATE = "state"
    MODEL = "model"
    LIVE = "live"


class NotReplayable(RuntimeError):
    pass


@dataclass(frozen=True)
class StateReplay:
    run_id: str
    ok: bool
    findings: tuple[str, ...]
    status_trail: tuple[str, ...]  # statuses reconstructed from events alone
    phases: dict[str, str]  # phase -> completed | skipped | failed | blocked | started
    events: int
    checkpoints: int
    reconstructed_status: str | None = None


def replay_state(run_id: str) -> StateReplay:
    """Reconstruct the run purely from its events and verify the history is internally consistent."""
    findings: list[str] = []
    with get_db() as conn:
        run = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise LookupError(run_id)
        events = ledger.read(conn, run_id, limit=1_000_000)
        cps = checkpoints.describe(conn, run_id)

    trail: list[str] = []
    phases: dict[str, str] = {}
    prev = RunStatus.CREATED
    last_attempt = 0
    uids: set[str] = set()
    for e in events:
        if e["event_uid"]:
            if e["event_uid"] in uids:
                findings.append(f"event {e['id']}: duplicate event id")
            uids.add(e["event_uid"])
        if e["attempt"] < last_attempt:
            findings.append(f"event {e['id']}: attempt number went backwards")
        last_attempt = max(last_attempt, e["attempt"])
        t = e["type"]
        if t == "run_state_changed":
            p = e["payload"] or {}
            try:
                src, dst = RunStatus(p["from"]), RunStatus(p["to"])
            except (KeyError, ValueError):
                findings.append(f"event {e['id']}: unreadable status change")
                continue
            if src is not prev:
                findings.append(f"event {e['id']}: status change starts from {src.value} but the run was {prev.value}")
            if not can_transition(src, dst):
                findings.append(f"event {e['id']}: {src.value} -> {dst.value} is not a legal transition")
            prev = dst
            trail.append(dst.value)
        elif t.startswith("phase_") and e["phase"]:
            phases[e["phase"]] = {"phase_started": "started", "phase_completed": "completed", "phase_skipped": "skipped",
                                  "phase_failed": "failed", "phase_blocked": "blocked"}.get(t, phases.get(e["phase"], "started"))

    reconstructed = trail[-1] if trail else None
    if reconstructed is not None and reconstructed != run["status"]:
        findings.append(f"the events end in '{reconstructed}' but the run record says '{run['status']}'")
    top = max((e["id"] for e in events), default=0)
    for cp in cps:
        if cp["status"] != "ok":
            findings.append(f"checkpoint {cp['seq']}: {cp['status']}")
        if cp["event_cursor"] > top:
            findings.append(f"checkpoint {cp['seq']} points past the end of the ledger")
    if run["status"] == "completed":
        findings += [f"phase '{p}' never finished" for p, s in phases.items() if s == "started"]
    return StateReplay(run_id, not findings, tuple(findings), tuple(trail), phases, len(events), len(cps), reconstructed)


def ensure_replayable(run_id: str) -> int:
    """Number of recorded model answers; raises ``NotReplayable`` when there is nothing to replay from."""
    from patchquest.agents.providers_recorded import load_recording

    with get_db() as conn:
        if conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone() is None:
            raise LookupError(run_id)
        calls = conn.execute("SELECT COUNT(*) FROM model_calls WHERE run_id = ?", (run_id,)).fetchone()[0]
    recorded = len(load_recording(run_id))
    if not calls:
        raise NotReplayable("this run made no recorded model calls")
    if not recorded:
        raise NotReplayable("the model's answers were not retained for this run (agent.record_model_io was off)")
    return recorded


@dataclass(frozen=True)
class Divergence:
    aspect: str  # verdict | diff | phases | repairs | model_calls
    original: Any
    replay: Any


@dataclass(frozen=True)
class Comparison:
    original_id: str
    replay_id: str
    divergences: tuple[Divergence, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> bool:
        return not self.divergences


def _signature(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run = conn.execute("SELECT verdict FROM runs WHERE id = ?", (run_id,)).fetchone()
    report = conn.execute("SELECT diff_patch FROM reports WHERE run_id = ?", (run_id,)).fetchone()
    events = ledger.read(conn, run_id, limit=1_000_000)
    shape = [(e["type"], e["phase"]) for e in events if e["type"] in _MILESTONES]
    return {
        "verdict": run["verdict"] if run else None,
        "diff": (report["diff_patch"] if report else None) or "",
        "phases": [f"{t}:{p}" for t, p in shape if t in ("phase_started", "phase_skipped")],
        "repairs": sum(1 for t, _ in shape if t == "repair_started"),
        "model_calls": conn.execute("SELECT COUNT(*) FROM model_calls WHERE run_id = ? AND status = 'ok'", (run_id,)).fetchone()[0],
    }


def compare_runs(original_id: str, replay_id: str) -> Comparison:
    """What differs between a run and its replay. The outcome is intentionally not compared: a replay
    never promotes, so 'applied' versus 'rejected' is by design, whereas the verdict and the diff are not."""
    with get_db() as conn:
        a, b = _signature(conn, original_id), _signature(conn, replay_id)
    found = tuple(Divergence(k, a[k], b[k]) for k in a if a[k] != b[k])
    return Comparison(original_id, replay_id, found)


def comparison_payload(c: Comparison) -> dict[str, Any]:
    def short(v: Any) -> Any:
        return v if not isinstance(v, str) or len(v) < 300 else v[:300] + "…"

    return {"matched": c.matched, "original": c.original_id, "replay": c.replay_id,
            "divergences": [{"aspect": d.aspect, "original": short(d.original), "replay": short(d.replay)} for d in c.divergences]}


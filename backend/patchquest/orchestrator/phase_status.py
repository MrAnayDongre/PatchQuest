"""Phase status tracking utilities."""

from __future__ import annotations

from patchquest.orchestrator.phases import PHASE_ORDER, Phase, PhaseStatus


def compute_progress(statuses: dict[Phase, PhaseStatus]) -> float:
    total = len(PHASE_ORDER)
    completed = sum(1 for s in statuses.values() if s in (PhaseStatus.COMPLETE, PhaseStatus.SKIPPED))
    return completed / total if total > 0 else 0.0


def get_phase_summary(statuses: dict[Phase, PhaseStatus]) -> list[dict[str, str]]:
    return [{"phase": p.value, "status": statuses[p].value} for p in PHASE_ORDER]

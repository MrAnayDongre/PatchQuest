"""Deciding whether, and how, an interrupted run can safely continue.

``plan_resume`` is read-only: it inspects the ledger, the newest *valid* checkpoint, the repository
and the promotion journal, and says what resuming would do and what it cannot be sure of. Nothing
that might repeat an external side effect with uncertain completion is ever classed as safe.

Promotion (the one write to the real repository) is journaled: ``promotion_started`` is appended
before any file is touched and ``promotion_completed``/``promotion_failed`` after. A crash in
between is settled by comparing each file's current hash with the journal's ``base``/``new`` hashes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from patchquest.database import get_db
from patchquest.domain.runs import RunStatus
from patchquest.orchestrator.phases import PHASE_ORDER, PhaseStatus
from patchquest.persistence import checkpoints, ledger
from patchquest.runtime import fingerprint
from patchquest.runtime.fingerprint import Drift, DriftReport


class RecoveryCategory(StrEnum):
    SAFE_RESUME = "SAFE_RESUME"  # continue from the checkpoint
    SAFE_RETRY = "SAFE_RETRY"  # no usable checkpoint, nothing external happened: start over
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"  # a partial write to the repository must be undone first
    HUMAN_CONFIRMATION_REQUIRED = "HUMAN_CONFIRMATION_REQUIRED"
    NON_RECOVERABLE = "NON_RECOVERABLE"


class PromotionState(StrEnum):
    NONE = "NONE"  # promotion never began
    NOT_APPLIED = "NOT_APPLIED"  # began, but the repository still holds the original files
    APPLIED = "APPLIED"  # the repository holds the new files
    PARTIAL = "PARTIAL"  # some files new, some original
    UNKNOWN = "UNKNOWN"  # a file holds content that is neither (someone else edited it)


class SideEffects(StrEnum):
    NONE = "NONE"  # nothing outside the shadow workspace was written
    VERIFIED = "VERIFIED"  # the repository write happened (or not) and the hashes prove which
    UNCERTAIN = "UNCERTAIN"


class NotResumable(RuntimeError):
    def __init__(self, plan: ResumePlan) -> None:
        super().__init__("; ".join(plan.reasons) or plan.category)
        self.plan = plan


class ConfirmationRequired(NotResumable):
    """Resuming is possible but needs an explicit human decision first."""


@dataclass(frozen=True)
class ResumePlan:
    run_id: str
    status: str
    category: RecoveryCategory
    checkpoint: dict[str, Any] | None
    invalid_checkpoints: tuple[str, ...]
    interrupted_operation: str | None
    drift: DriftReport | None
    promotion: PromotionState
    side_effects: SideEffects
    next_phase: str | None
    recovery_action: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    promotion_files: dict[str, dict[str, str | None]] = field(default_factory=dict)

    @property
    def needs_confirmation(self) -> bool:
        return self.category in (RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED, RecoveryCategory.ROLLBACK_REQUIRED)

    def explain(self) -> dict[str, Any]:
        """The fixed vocabulary shown to the user before anything runs."""
        cp = self.checkpoint
        return {
            "LAST_CHECKPOINT": (f"#{cp['seq']} after {cp['phase']} ({cp['created_at']})" if cp else "none"),
            "INTERRUPTED_OPERATION": self.interrupted_operation or "none recorded",
            "REPO_DRIFT": self.drift.kind.value if self.drift else "NOT_CHECKED",
            "SIDE_EFFECT_CERTAINTY": self.side_effects.value,
            "RECOVERY_ACTION": self.recovery_action,
            "APPROVAL_REQUIRED": self.needs_confirmation,
            "CATEGORY": self.category.value,
            "REASONS": list(self.reasons),
        }


def _tail_description(tail: list[dict[str, Any]]) -> str | None:
    """What the run was doing when it stopped, from the events after its last checkpoint."""
    open_command: str | None = None
    phase: str | None = None
    waiting: str | None = None
    for e in tail:
        t = e["type"]
        if t == "phase_started":
            phase, open_command, waiting = e["phase"], None, None
        elif t == "command_started":
            open_command = (e.get("payload") or {}).get("command")
        elif t in ("command_executed", "command_denied", "command_blocked"):
            open_command = None
        elif t == "approval_requested":
            waiting = e.get("message")
        elif t in ("approval_expired", "permission_approved", "permission_rejected"):
            waiting = None
    if phase is None:
        return None
    detail = (f"; command `{open_command}` was running" if open_command
              else f"; waiting for approval: {waiting}" if waiting else "")
    return f"phase '{phase}'{detail}"


def _promotion(events: list[dict[str, Any]], repo: str) -> tuple[PromotionState, dict[str, dict[str, str | None]]]:
    started = next((e for e in reversed(events) if e["type"] == "promotion_started"), None)
    if started is None:
        return PromotionState.NONE, {}
    files: dict[str, dict[str, str | None]] = (started.get("payload") or {}).get("files") or {}
    closed = [e["type"] for e in events if e["id"] > started["id"] and e["type"] in ("promotion_completed", "promotion_failed")]
    if closed and closed[-1] == "promotion_completed":
        return PromotionState.APPLIED, files
    now = {rel: fingerprint.file_sha(repo, rel) for rel in files}
    at_base = [rel for rel, h in files.items() if now[rel] == h["base"]]
    at_new = [rel for rel, h in files.items() if now[rel] == h["new"] and h["new"] != h["base"]]
    if len(at_base) == len(files):
        return PromotionState.NOT_APPLIED, files
    if len(at_new) == len(files):
        return PromotionState.APPLIED, files
    if len(at_base) + len(at_new) == len(files):
        return PromotionState.PARTIAL, files
    return PromotionState.UNKNOWN, files


def _next_phase(cp: checkpoints.Checkpoint | None) -> str | None:
    if cp is None:
        return PHASE_ORDER[0].value
    statuses = cp.state.get("phase_statuses") or {}
    for phase in PHASE_ORDER:
        if statuses.get(phase.value, PhaseStatus.PENDING.value) == PhaseStatus.PENDING.value:
            return phase.value
    return None


def _plan(run_id: str, status: str, category: RecoveryCategory, action: str, reasons: list[str], **kw: Any) -> ResumePlan:
    defaults: dict[str, Any] = {"checkpoint": None, "invalid_checkpoints": (), "interrupted_operation": None, "drift": None,
                                "promotion": PromotionState.NONE, "side_effects": SideEffects.NONE, "next_phase": None}
    return ResumePlan(run_id, status, category, recovery_action=action, reasons=tuple(reasons), **{**defaults, **kw})


def plan_resume(run_id: str) -> ResumePlan:
    with get_db() as conn:
        run = conn.execute("SELECT status, repo_path FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise LookupError(run_id)
        events = ledger.read(conn, run_id, limit=100_000)
        cp, problems = checkpoints.latest_valid(conn, run_id)
    status = RunStatus(run["status"])

    if status is not RunStatus.INTERRUPTED:
        why = ("the run is still active" if status in (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL, RunStatus.CANCEL_REQUESTED)
               else f"the run already ended as {status.value}")
        return _plan(run_id, status.value, RecoveryCategory.NON_RECOVERABLE,
                     "Nothing to resume; fork the run to try again from a checkpoint" if status is not RunStatus.CREATED
                     else "Start the run instead", [why])

    repo = run["repo_path"]
    promotion, files = _promotion(events, repo)
    tail = [e for e in events if cp is None or e["id"] > cp.event_cursor]
    common: dict[str, Any] = {
        "checkpoint": ({"seq": cp.seq, "phase": cp.phase, "created_at": cp.created_at, "event_cursor": cp.event_cursor}
                       if cp else None),
        "invalid_checkpoints": tuple(problems), "interrupted_operation": _tail_description(tail),
        "promotion": promotion, "next_phase": _next_phase(cp), "promotion_files": files}
    reasons = [f"skipped an unusable checkpoint: {p}" for p in problems]

    if promotion is PromotionState.PARTIAL:
        return _plan(run_id, status.value, RecoveryCategory.ROLLBACK_REQUIRED,
                     "Undo the partial write to the repository, then resume",
                     [*reasons, "the interruption left the repository partly patched"],
                     side_effects=SideEffects.UNCERTAIN, **common)
    if promotion is PromotionState.UNKNOWN:
        return _plan(run_id, status.value, RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED,
                     "Inspect the files, then confirm",
                     [*reasons, "a patched file holds content that is neither the original nor the proposed version"],
                     side_effects=SideEffects.UNCERTAIN, **common)
    effects = SideEffects.VERIFIED if promotion is not PromotionState.NONE else SideEffects.NONE
    if promotion is PromotionState.APPLIED:
        reasons.append("the patch had already been written to the repository; it will not be written again")
    elif promotion is PromotionState.NOT_APPLIED:
        reasons.append("promotion had begun but the repository still holds the original files")

    if cp is None and promotion is not PromotionState.NONE:
        return _plan(run_id, status.value, RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED,
                     "Inspect the repository; there is no checkpoint to continue from",
                     [*reasons, "promotion was recorded but no usable checkpoint remains to reconcile it with"],
                     side_effects=effects, **common)
    if cp is None:
        return _plan(run_id, status.value, RecoveryCategory.SAFE_RETRY, "Start the run again from the beginning",
                     [*reasons, "no usable checkpoint"], side_effects=effects, **common)

    touched = list(cp.state.get("workspace") or {})
    recorded = fingerprint.RepoFingerprint.from_dict(cp.fingerprint)
    if promotion is PromotionState.APPLIED:
        # Our own promotion changed these files; that is the expected state, not drift.
        recorded = replace(recorded, files={**recorded.files, **{rel: h["new"] for rel, h in files.items()}})
    drift = fingerprint.classify(recorded, fingerprint.compute(repo, recorded.files), touched)
    reasons += list(drift.reasons)
    if drift.kind in (Drift.CONFLICTING_DRIFT, Drift.UNKNOWN_DRIFT):
        return _plan(run_id, status.value, RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED,
                     "Review the changes made while the run was down, then confirm; promotion still refuses "
                     "to overwrite files that changed", reasons, side_effects=effects, drift=drift, **common)
    return _plan(run_id, status.value, RecoveryCategory.SAFE_RESUME, f"Continue from phase '{common['next_phase']}'",
                 reasons, side_effects=effects, drift=drift, **common)


def revert_partial_promotion(run_id: str, plan: ResumePlan) -> list[str]:
    """Put back the original content of every file the interrupted promotion had already replaced.

    Only files whose current content is exactly what the journal says PatchQuest wrote are touched, and
    the write itself is hash-checked, so a human's edits are never clobbered. Returns the files reverted.
    """
    from patchquest.patching import DeleteFile, WriteFile, apply_changes

    with get_db() as conn:
        cp, _ = checkpoints.latest_valid(conn, run_id)
        repo = conn.execute("SELECT repo_path FROM runs WHERE id = ?", (run_id,)).fetchone()["repo_path"]
    if cp is None or not cp.state.get("workspace"):
        raise NotResumable(plan)  # without the originals there is nothing to restore
    import base64

    changes: list[Any] = []
    expected: dict[str, str | None] = {}
    for rel, h in plan.promotion_files.items():
        if fingerprint.file_sha(repo, rel) != h["new"] or h["new"] == h["base"]:
            continue
        original = cp.state["workspace"].get(rel, {}).get("base")
        if original is None:
            changes.append(DeleteFile(rel))
        else:
            changes.append(WriteFile(rel, base64.b64decode(original).decode("utf-8")))
        expected[rel] = h["new"]
    if changes:
        result = apply_changes(repo, changes, expected_hashes=expected)
        if not result.success:
            raise NotResumable(plan) from RuntimeError(result.error)
    with get_db() as conn:
        ledger.append(conn, run_id, "promotion_rolled_back", phase="final_report", actor="user",
                      message=f"Restored {len(expected)} file(s) left half-patched by the interruption",
                      payload={"files": sorted(expected)})
    return sorted(expected)

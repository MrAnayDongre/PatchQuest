"""TaskService: create, run, control, approve and inspect runs.

This is the single place run behaviour lives. The HTTP routes, the scheduler and the CLI are
thin adapters over it, so a run behaves the same however it was started. It returns plain
dicts (no HTTP or ORM types) so any interface can serialise them.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from patchquest.agents.providers_recorded import RecordedProvider, session_name
from patchquest.config import validate_overrides
from patchquest.database import get_db, insert_event, now_iso
from patchquest.domain.approvals import Decision
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.runs import RunStatus
from patchquest.orchestrator.event_bus import event_bus
from patchquest.orchestrator.state_machine import RunStateMachine
from patchquest.persistence import approvals, checkpoints, ledger, tenancy
from patchquest.persistence.runs import transition
from patchquest.runtime import lineage, queue
from patchquest.runtime import policy as policy_runtime
from patchquest.runtime.fingerprint import Drift, DriftReport
from patchquest.runtime.replay import (
    REPLAY_OVERRIDES,
    ReplayMode,
    StateReplay,
    compare_runs,
    comparison_payload,
    ensure_replayable,
    replay_state,
)
from patchquest.runtime.resume import (
    ConfirmationRequired,
    NotResumable,
    PromotionState,
    RecoveryCategory,
    ResumePlan,
    assess_drift,
    plan_resume,
    revert_partial_promotion,
)
from patchquest.security import validate_base_url, validate_repo_path

TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_interrupted"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


class RunNotFound(LookupError):
    pass


class RunNotActive(RuntimeError):
    pass


class ForkError(RuntimeError):
    pass


class ForkBlocked(RuntimeError):
    """The repository changed under the checkpoint; forking needs an explicit decision."""

    def __init__(self, drift: DriftReport) -> None:
        super().__init__("; ".join(drift.reasons) or drift.kind.value)
        self.drift = drift


def _row(r: Any) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


class TaskService:
    def __init__(self) -> None:
        self._machines: dict[str, RunStateMachine] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ create
    def create_run(
        self,
        *,
        repo_path: str,
        task: str,
        provider: str = "mock",
        model: str | None = None,
        runtime_mode: str = "local",
        model_profile: str | None = None,
        memory_mode: str = "repo",
        allow_network: bool = False,
        dry_run: bool = False,
        base_url: str | None = None,
        workspace_id: str = LOCAL_WORKSPACE_ID,
        created_by: str | None = None,
        overrides: dict[str, Any] | None = None,
        acting_as: str | None = None,
    ) -> dict[str, Any]:
        """Validate and persist a run in state ``created``. Raises RepoPathError on a bad path, and
        RepositoryNotRegistered / ProjectAccessDenied when the workspace may not act on that repository.
        ``acting_as`` names the person a workflow is running for, for the project-team check."""
        repo_path = validate_repo_path(repo_path)
        with get_db() as conn:
            tenancy.check_run_allowed(conn, workspace_id=workspace_id, repo_path=repo_path, actor=acting_as or created_by)
        base_url = validate_base_url(base_url)
        overrides = policy_runtime.clamp_overrides(
            validate_overrides(overrides) if overrides else None,
            policy_runtime.chain_for(workspace_id=workspace_id, repo_path=repo_path, user=created_by))
        overrides_json = json.dumps(overrides) if overrides else None
        run_id = str(uuid.uuid4())
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO runs (id, repo_path, task, status, provider, model, model_profile,
                   memory_mode, runtime_mode, allow_network, dry_run, base_url, created_at, updated_at,
                   workspace_id, created_by, overrides_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, repo_path, task, "created", provider, model, model_profile, memory_mode,
                 runtime_mode, int(allow_network), int(dry_run), base_url, now, now, workspace_id, created_by, overrides_json),
            )
            insert_event(conn, run_id, "run_created", message=f"Run created: {task[:100]}", actor=created_by or "runtime")
        return self.get_run(run_id)

    def _machine_for(self, run: dict[str, Any]) -> RunStateMachine:
        return RunStateMachine(
            run["id"], run["repo_path"], run["task"], provider=run["provider"] or "mock", model=run["model"],
            runtime_mode=run["runtime_mode"] or "local", dry_run=bool(run["dry_run"]), base_url=run.get("base_url"),
            overrides=json.loads(run["overrides_json"]) if run.get("overrides_json") else None,
        )

    # --------------------------------------------------------------------- run
    def launch(self, run_id: str) -> None:
        """Start the run in the background of the current event loop."""
        machine = self._machine_for(self.get_run(run_id))
        self._machines[run_id] = machine
        task = asyncio.get_running_loop().create_task(machine.execute())
        self._tasks[run_id] = task  # strong reference: a bare create_task() may be GC'd mid-run

        def _cleanup(_t: asyncio.Task) -> None:
            self._machines.pop(run_id, None)
            self._tasks.pop(run_id, None)

        task.add_done_callback(_cleanup)

    def budget(self, run_id: str) -> list[dict[str, Any]]:
        """Budget consumption: live for an active run, else as of the run's last checkpoint."""
        machine = self._machines.get(run_id)
        if machine is not None:
            return [line.to_dict() for line in machine.budget_lines()]
        with get_db() as conn:
            row = conn.execute("SELECT payload_json FROM run_events WHERE run_id = ? AND type = 'checkpoint_created' "
                               "ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        return (json.loads(row["payload_json"]).get("budget") or []) if row and row["payload_json"] else []

    def resume(self, run_id: str, *, accept_drift: bool = False, rollback: bool = False, defer: bool = False) -> ResumePlan:
        """Continue an interrupted run after the safety checks in ``plan_resume``.

        Raises ``NotResumable`` when it cannot, ``ConfirmationRequired`` when it can but a human must first
        accept ``accept_drift`` (changes made while the run was down) or ``rollback`` (undo a half-written
        promotion). Returns the plan that was acted on. With ``defer`` the run is queued for a worker instead of
        being continued in this process.
        """
        from patchquest.persistence import checkpoints

        if run_id in self._machines:
            raise NotResumable(plan_resume(run_id))
        plan = plan_resume(run_id)
        if plan.category is RecoveryCategory.NON_RECOVERABLE:
            raise NotResumable(plan)
        if plan.category is RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED and not accept_drift:
            raise ConfirmationRequired(plan)
        if plan.category is RecoveryCategory.ROLLBACK_REQUIRED:
            if not rollback:
                raise ConfirmationRequired(plan)
            revert_partial_promotion(run_id, plan)

        run = self.get_run(run_id)
        machine = self._machine_for(run)
        machine.attempt = int(run.get("attempt") or 1) + 1
        reconciled = plan.promotion is PromotionState.APPLIED
        with get_db() as conn:
            conn.execute("UPDATE runs SET attempt = ? WHERE id = ?", (machine.attempt, run_id))
            cp, _ = checkpoints.latest_valid(conn, run_id)
            insert_event(conn, run_id, "run_resume_requested", message=plan.recovery_action,
                         payload=plan.explain(), actor="user", attempt=machine.attempt)
            how = f"resumed from checkpoint {cp.seq}" if cp else "restarted from the beginning"
            if defer:
                conn.execute("UPDATE runs SET resume_note = ? WHERE id = ?", ("applied" if reconciled else None, run_id))
                queue.enqueue(conn, run_id, actor="resume", reason=f"{how}; queued for a worker")
                return plan
            # Leave "interrupted" before returning: anything following the run (CLI, SSE) treats that
            # status as the end of the stream.
            transition(conn, run_id, RunStatus.RUNNING, actor="resume", attempt=machine.attempt,
                       correlation_id=machine.correlation_id, reason=how)

        def configure(m: RunStateMachine) -> None:
            if reconciled:  # applied after restore: a checkpoint carries the flag's older value
                m._promotion_reconciled = "applied"

        self._spawn(run_id, machine, checkpoint=cp, resumed=True, configure=configure)
        return plan

    def enqueue(self, run_id: str, actor: str = "runtime") -> None:
        """Hand a created run to the worker pool instead of running it in this process."""
        with get_db() as conn:
            queue.enqueue(conn, run_id, actor=actor)

    def start_claimed(self, run_id: str) -> asyncio.Task[None]:
        """Run a run a worker has just claimed (it is already ``running``). Continues from the run's newest valid
        checkpoint if it has one (a fork, or a resume), else starts from the beginning."""
        from patchquest.persistence import checkpoints

        run = self.get_run(run_id)
        machine = self._machine_for(run)
        machine.attempt = int(run.get("attempt") or 1)
        with get_db() as conn:
            cp, _ = checkpoints.latest_valid(conn, run_id)

        def configure(m: RunStateMachine) -> None:
            if run.get("resume_note") == "applied":
                m._promotion_reconciled = "applied"

        self._spawn(run_id, machine, checkpoint=cp, resumed=True, configure=configure)
        return self._tasks[run_id]

    def _spawn(self, run_id: str, machine: RunStateMachine, *, checkpoint: checkpoints.Checkpoint | None = None,
               resumed: bool = False, configure: Callable[[RunStateMachine], None] | None = None,
               on_done: Callable[[], None] | None = None) -> None:
        """Run ``machine`` as a background task, optionally from a checkpoint, tracked until it ends."""
        self._machines[run_id] = machine

        async def _go() -> None:
            try:
                if checkpoint is not None:
                    await machine.restore_checkpoint(checkpoint)
                if configure is not None:
                    configure(machine)
                await machine.execute(resumed=resumed)
            finally:
                if on_done is not None:
                    on_done()

        task = asyncio.get_running_loop().create_task(_go())
        self._tasks[run_id] = task  # strong reference: a bare create_task() may be GC'd mid-run

        def _cleanup(_t: asyncio.Task) -> None:
            self._machines.pop(run_id, None)
            self._tasks.pop(run_id, None)

        task.add_done_callback(_cleanup)

    # ------------------------------------------------------------- fork / replay
    def fork(self, run_id: str, *, from_seq: int | None = None, provider: str | None = None, model: str | None = None,
             base_url: str | None = None, overrides: dict[str, Any] | None = None,
             accept_drift: bool = False, defer: bool = False) -> dict[str, Any]:
        """Start a new run from one of ``run_id``'s checkpoints, optionally with another model or settings.

        The parent is never modified. Raises ``ForkError`` when there is no usable checkpoint, and
        ``ForkBlocked`` when the repository changed under the checkpoint and ``accept_drift`` is not set.
        """
        parent = self.get_run(run_id)
        base_url = validate_base_url(base_url) if base_url else None
        validate_overrides(overrides or {})  # a typo is a usage error, whatever state the repository is in
        with get_db() as conn:
            try:
                cp = checkpoints.get(conn, run_id, from_seq) if from_seq is not None else checkpoints.latest_valid(conn, run_id)[0]
            except (LookupError, checkpoints.CheckpointError) as exc:
                raise ForkError(f"checkpoint {from_seq} cannot be used: {exc}") from exc
        if cp is None:
            raise ForkError("this run has no usable checkpoint to fork from")
        drift = assess_drift(parent["repo_path"], cp)
        if drift.kind in (Drift.CONFLICTING_DRIFT, Drift.UNKNOWN_DRIFT) and not accept_drift:
            raise ForkBlocked(drift)
        with get_db() as conn:
            child_id = lineage.create_child(conn, run_id, kind="fork", parent_cp=cp, provider=provider, model=model,
                                            base_url=base_url, overrides=overrides)
        if defer:  # a worker restores the child's copy of the fork-point checkpoint when it claims the run
            self.enqueue(child_id)
            return self.get_run(child_id)
        child = self.get_run(child_id)
        self._spawn(child_id, self._machine_for(child), checkpoint=cp)
        return child

    def replay(self, run_id: str, mode: ReplayMode) -> StateReplay | dict[str, Any]:
        """``STATE`` verifies the ledger and returns a report. ``MODEL``/``LIVE`` start a child run that never
        promotes to the repository; compare it with ``compare_replay`` once it ends."""
        if mode is ReplayMode.STATE:
            return replay_state(run_id)
        self.get_run(run_id)
        child_id = str(uuid.uuid4())
        provider: str | None = None  # live replay: the original run's own provider and model
        model: str | None = None
        if mode is ReplayMode.MODEL:
            ensure_replayable(run_id)
            provider, model = "recorded", session_name(run_id, child_id)
        with get_db() as conn:
            lineage.create_child(conn, run_id, kind="replay", parent_cp=None, provider=provider, model=model,
                                 overrides=REPLAY_OVERRIDES, replay_mode=mode.value, run_id=child_id, actor="replay")
        child = self.get_run(child_id)

        def finish() -> None:
            RecordedProvider.end_session(session_name(run_id, child_id))
            comparison = compare_runs(run_id, child_id)
            with get_db() as conn:
                ledger.append(conn, child_id, "replay_completed" if comparison.matched else "replay_diverged",
                              actor="replay", message="Matches the original run" if comparison.matched
                              else "Differs from the original run", payload=comparison_payload(comparison))

        replay_base = self._original_files(run_id)

        def configure(m: RunStateMachine) -> None:
            m.replay_base = replay_base

        self._spawn(child_id, self._machine_for(child), configure=configure, on_done=finish)
        return child

    @staticmethod
    def _original_files(run_id: str) -> dict[str, bytes | None]:
        """What the files ``run_id`` changed looked like before it changed them (from its checkpoints)."""
        with get_db() as conn:
            cp, _ = checkpoints.latest_valid(conn, run_id)
        touched = (cp.state.get("workspace") or {}) if cp else {}
        return {rel: (base64.b64decode(v["base"]) if v["base"] is not None else None) for rel, v in touched.items()}

    def lineage(self, run_id: str) -> dict[str, Any]:
        with get_db() as conn:
            return {"ancestry": lineage.lineage(conn, run_id), "children": lineage.children(conn, run_id)}

    async def run_to_completion(self, run_id: str) -> dict[str, Any]:
        self.launch(run_id)
        await self._tasks[run_id]
        return self.get_run(run_id)

    def cancel(self, run_id: str) -> None:
        machine = self._machines.get(run_id)
        if machine is None:
            raise RunNotActive(run_id)
        machine.cancel()

    async def shutdown(self) -> None:
        for machine in list(self._machines.values()):
            machine.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def is_active(self, run_id: str) -> bool:
        return run_id in self._machines

    # ---------------------------------------------------------------- approvals
    async def decide(self, run_id: str, approval_id: str, decision: Decision, *, actor: str = "user",
                     note: str | None = None, modified_command: str | None = None) -> approvals.Outcome:
        """Record a decision and wake the run waiting for it. Raises ``ApprovalError`` subclasses (stable
        ``code`` attribute) when the decision cannot stand: unknown, already decided, expired, not allowed."""
        self.get_run(run_id)
        with get_db() as conn:
            outcome = approvals.decide(conn, run_id, approval_id, decision, actor=actor, note=note,
                                       modified_command=modified_command)
        machine = self._machines.get(run_id)
        if machine:
            await machine.resolve_approval(approval_id, outcome)
        return outcome

    async def approve(self, run_id: str, approval_id: str, approved: bool, note: str | None = None) -> None:
        """Yes/no shorthand for ``decide``."""
        await self.decide(run_id, approval_id, Decision.APPROVE_ONCE if approved else Decision.DENY, note=note)

    def pending_approvals(self, run_id: str) -> list[dict[str, Any]]:
        with get_db() as conn:
            return approvals.pending(conn, run_id)

    # ------------------------------------------------------------------ inspect
    def get_run(self, run_id: str) -> dict[str, Any]:
        with get_db() as conn:
            r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            raise RunNotFound(run_id)
        return _row(r)

    def list_runs(self, limit: int = 50, workspace_ids: list[str] | None = None, before: str | None = None) -> list[dict[str, Any]]:
        """Newest first. ``workspace_ids`` restricts to those workspaces (None: all; []: none). ``before`` is the
        ``created_at`` of the last run already seen, to page backwards."""
        clauses: list[str] = []
        params: list[Any] = []
        if workspace_ids is not None:
            clauses.append(f"workspace_id IN ({','.join('?' * len(workspace_ids)) or 'NULL'})")
            params += workspace_ids
        if before:
            clauses.append("created_at < ?")
            params.append(before)
        sql = "SELECT * FROM runs" + (f" WHERE {' AND '.join(clauses)}" if clauses else "")
        with get_db() as conn:
            rows = conn.execute(sql + " ORDER BY created_at DESC LIMIT ?", [*params, limit]).fetchall()
        return [_row(r) for r in rows]

    def events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id",
                                (run_id, after_id)).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            d["payload"] = json.loads(d.pop("payload_json")) if d.get("payload_json") else None
            out.append(d)
        return out

    def report(self, run_id: str) -> dict[str, Any] | None:
        with get_db() as conn:
            r = conn.execute("SELECT * FROM reports WHERE run_id = ?", (run_id,)).fetchone()
        return _row(r) if r else None

    def diff(self, run_id: str) -> str:
        report = self.report(run_id)
        return (report or {}).get("diff_patch") or ""

    async def stream(self, run_id: str, after_id: int = 0, heartbeat: float = 15.0) -> AsyncIterator[dict[str, Any]]:
        """Replay persisted events, then follow live ones; ends after the run's terminal event.

        Subscribing *before* replaying closes the gap in which an event could be missed, and ids
        de-duplicate the overlap. A client that connects after the run finished still receives
        the complete history and the stream ends.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        event_bus.subscribe(run_id, queue)
        try:
            last = after_id
            for event in self.events(run_id, after_id):
                last = event["id"]
                yield event
                if event["type"] in TERMINAL_EVENTS:
                    return
            if self.get_run(run_id)["status"] in TERMINAL_STATUSES:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield {"type": "ping", "run_id": run_id}
                    if not self.is_active(run_id) and self.get_run(run_id)["status"] in TERMINAL_STATUSES:
                        return
                    continue
                if event.get("id", 0) <= last:
                    continue
                last = event["id"]
                yield event
                if event["type"] in TERMINAL_EVENTS:
                    return
        finally:
            event_bus.unsubscribe(run_id, queue)


_service: TaskService | None = None


def get_service() -> TaskService:
    global _service
    if _service is None:
        _service = TaskService()
    return _service

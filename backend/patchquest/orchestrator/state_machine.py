"""Deterministic state machine for orchestrating a coding run.

Mutating runs never touch the real repository until the change has been validated:

    context (real files) -> plan -> patch in a shadow workspace -> static checks + tests
    -> bounded repair loop -> baseline comparison on failure -> review -> security scan
    -> promote to the repo (sha256-checked) or leave the diff for a human

Every command passes the policy gate (``tools.command_risk``); anything that needs a human
creates a persisted approval and waits, with a timeout that means "denied".
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from typing import TYPE_CHECKING, Any

from patchquest import __version__
from patchquest.config import config_overrides, get_config, validate_overrides
from patchquest.database import get_db, now_iso
from patchquest.domain import budget
from patchquest.domain.failures import Failure, FailureKind, PatchQuestError, classify
from patchquest.domain.runs import IllegalTransition, RunStatus
from patchquest.orchestrator import snapshot
from patchquest.orchestrator.event_bus import event_bus
from patchquest.orchestrator.phases import PHASE_ORDER, Phase, PhaseStatus
from patchquest.orchestrator.run_context import RunContext
from patchquest.persistence import checkpoints, ledger
from patchquest.persistence.runs import transition
from patchquest.tools.secret_guard import redact_secrets

if TYPE_CHECKING:
    from patchquest.runtime.workspace import ShadowWorkspace

logger = logging.getLogger(__name__)

_PLAN_REQUIRED_KEYS = {"plan", "files_to_inspect", "tests_likely_needed",
                       "expected_patch_scope", "stop_conditions", "test_commands"}

# After a phase blocks the run, only these still execute (so the report and scan are produced).
_PHASES_AFTER_BLOCK = {Phase.SECURITY_SCAN, Phase.FINAL_REPORT}
# passed: everything green. no_tests: nothing to run. unresolved: only failures that also occur
# without the patch remain (it caused no regression, but may not have fixed the target either).
_PROMOTABLE_VERDICTS = {"passed", "no_tests"}


_EMPTY_PATCH_HINT = (
    "Your previous reply contained no edits, which means 'no change is needed'. This task does require "
    "a change. Reply with at least one entry in \"edits\" (copy the \"search\" text exactly from the file "
    "shown) or \"create\", or explain in \"rationale\" why no change is needed."
)


def _says_no_change(output: dict[str, Any]) -> bool:
    return "no change" in str(output.get("rationale", "")).lower()


def _tool_available(command: str) -> bool:
    """True when the command's executable resolves on the (scrubbed) PATH.

    A tool that is not installed is an environment condition, not a failing test, so such
    commands are never run (and never counted as failures).
    """
    import shlex
    import shutil

    from patchquest.execution.executor import scrubbed_env

    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return bool(argv) and shutil.which(argv[0], path=scrubbed_env().get("PATH")) is not None


class PhaseBlockedError(Exception):
    """A phase cannot proceed without something the run does not have (e.g. a denied approval)."""


def _normalize_plan(raw: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """Normalize an LLM planning response into a safe Plan dict.

    Handles both well-formed JSON and free-form text (parse_error=True).
    """
    if raw.get("parse_error"):
        raw_text = raw.get("raw_response", "")
        summary = raw_text[:500] if raw_text else "Planning output was not valid JSON."
        return {
            "plan": summary,
            "files_to_inspect": [],
            "tests_likely_needed": [],
            "expected_patch_scope": "no modifications" if ctx.read_only else "unknown",
            "stop_conditions": ["read-only task completed"] if ctx.read_only else ["task completed"],
            "test_commands": [],
            "parse_error": True,
        }

    result: dict[str, Any] = {}
    result["plan"] = raw.get("plan", str(raw)[:500])
    result["files_to_inspect"] = _ensure_list(raw.get("files_to_inspect"))
    result["tests_likely_needed"] = _ensure_list(raw.get("tests_likely_needed"))
    result["expected_patch_scope"] = raw.get("expected_patch_scope", "")
    if ctx.read_only and not result["expected_patch_scope"]:
        result["expected_patch_scope"] = "no modifications"
    result["stop_conditions"] = _ensure_list(raw.get("stop_conditions"))
    result["test_commands"] = _ensure_list(raw.get("test_commands"))
    if raw.get("parse_error"):
        result["parse_error"] = True
    return result


def _ensure_list(val: Any) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [val] if val else []
    if val is None:
        return []
    return [str(val)]


class RunStateMachine:
    def __init__(
        self,
        run_id: str,
        repo_path: str,
        task: str,
        provider: str = "mock",
        model: str | None = None,
        runtime_mode: str = "local",
        dry_run: bool = False,
        base_url: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        from patchquest.orchestrator.run_context import _detect_read_only

        self.run_id = run_id
        self.overrides = validate_overrides(overrides) if overrides else {}
        read_only = _detect_read_only(task, dry_run)
        self.ctx = RunContext(
            run_id=run_id, repo_path=repo_path, task=task,
            provider=provider, model=model, runtime_mode=runtime_mode,
            dry_run=dry_run, read_only=read_only, base_url=base_url,
        )
        self.phase_statuses: dict[Phase, PhaseStatus] = {p: PhaseStatus.PENDING for p in Phase}
        self._approval_events: dict[str, asyncio.Event] = {}
        self._approval_results: dict[str, bool] = {}
        self._workspace: ShadowWorkspace | None = None  # created on demand
        self._blocked = False
        self._current_phase: str | None = None
        self._patch_secret = False  # the patch itself introduced (or tried to introduce) a secret
        self._promotion_reconciled: str | None = None  # "applied": resume found promotion had already landed
        self._attempt_clock = time.monotonic()  # wall time is charged to the budget in ticks
        # Replay only: the original run's pre-change file contents, laid over the shadow workspace so the
        # replay starts from the files the original saw even though the real repository has since moved on.
        self.replay_base: dict[str, bytes | None] | None = None
        self._failure: Failure | None = None  # the first thing that went wrong; becomes the run's failure kind
        self._no_patch = False  # a mutating task for which the agent produced nothing and said nothing
        self._cancelled = asyncio.Event()
        self.attempt = 1  # incremented by resume; every event carries it
        self.correlation_id = uuid.uuid4().hex  # groups the events of this execution
        self._phase_event_uid: str | None = None  # causation for everything inside the current phase
        self.ctx.event_sink = self._publish

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        await self._emit(event_type, phase=self._current_phase, message=payload.get("role"), payload=payload)

    # ------------------------------------------------------------------ driver
    def cancel(self) -> None:
        """Request cooperative cancellation; takes effect at the next phase boundary."""
        self._cancelled.set()
        self._move(RunStatus.CANCEL_REQUESTED, "cancel requested", actor="user")
        for event in self._approval_events.values():  # unblock anything waiting on a human
            event.set()

    def _move(self, target: RunStatus, reason: str, *, actor: str = "runtime",
              fields: dict[str, Any] | None = None) -> bool:
        """Change the run's status through the validated table. False when that is no longer legal
        (e.g. the run already finished), which callers treat as 'nothing to do'."""
        try:
            with get_db() as conn:
                done = transition(conn, self.run_id, target, actor=actor, reason=reason, attempt=self.attempt,
                                  correlation_id=self.correlation_id, fields=fields)
            event_bus.emit_nowait(self.run_id, {
                "id": done.event_id, "event_uid": done.event_uid, "type": "run_state_changed", "run_id": self.run_id,
                "phase": None, "status": target.value, "message": reason,
                "payload": {"from": done.previous.value, "to": target.value}})
            return True
        except IllegalTransition as exc:
            logger.info("status change skipped: %s", exc)
            return False

    async def execute(self, resumed: bool = False) -> None:
        with config_overrides(self.overrides):  # this run's settings, visible to everything it awaits
            await self._execute(resumed)

    async def _execute(self, resumed: bool) -> None:
        if not resumed:  # a resumed run was already moved to RUNNING by TaskService.resume
            self._move(RunStatus.RUNNING, "execution started")
        try:
            for phase in PHASE_ORDER:
                if self.phase_statuses[phase] is not PhaseStatus.PENDING:
                    continue  # settled before the checkpoint this run was resumed from
                if self._cancelled.is_set():
                    await self._fail_run("Run cancelled", status="cancelled")
                    return
                self._enforce(budget.BudgetKind.WALL_TIME)
                if self._blocked and phase not in _PHASES_AFTER_BLOCK:
                    await self._skip(phase, "Skipped: an earlier phase is blocked")
                    await self._checkpoint(phase)
                    continue
                await self._run_phase(phase)
                if self.phase_statuses[phase] == PhaseStatus.FAILED:
                    await self._fail_run(f"Phase {phase.value} failed")
                    return
                if self.phase_statuses[phase] == PhaseStatus.BLOCKED:
                    self._blocked = True
                await self._checkpoint(phase)

            await self._complete_run()
        except Exception as e:
            logger.error(f"Run {self.run_id} crashed: {e}\n{traceback.format_exc()}")
            self._failure = self._failure or classify(e)
            await self._fail_run(str(e))
        finally:
            if self._workspace is not None:
                await asyncio.to_thread(self._workspace.cleanup)

    # ------------------------------------------------------------------ budgets
    def _tick(self) -> None:
        now = time.monotonic()
        self.ctx.wall_seconds += now - self._attempt_clock
        self._attempt_clock = now

    def budget_lines(self) -> list[budget.BudgetLine]:
        self._tick()
        return budget.snapshot(self.ctx, get_config().agent)

    def _enforce(self, *kinds: budget.BudgetKind) -> None:
        """Stop the run (BUDGET_EXHAUSTED) if any of the budgets about to be spent is used up."""
        self._tick()
        line = budget.exhausted(self.ctx, get_config().agent, among=kinds)
        if line is not None:
            raise PatchQuestError(FailureKind.BUDGET_EXHAUSTED,
                                  f"{line.kind.value} budget exhausted ({line.used:g} of {line.limit:g})")

    # -------------------------------------------------------------- checkpoints
    async def _checkpoint(self, phase: Phase) -> None:
        """Persist everything needed to continue after ``phase``. A failure here is recorded, never fatal:
        the run keeps going, it is just resumable only from an earlier checkpoint."""
        from patchquest.runtime import fingerprint

        try:
            state = await asyncio.to_thread(snapshot.capture, self)
            ws = self._workspace
            watch = {*self.ctx.selected_context, *self.ctx.selected_files, *(ws.touched if ws else ())}
            fp = await asyncio.to_thread(fingerprint.compute, self.ctx.repo_path, watch)
            with get_db() as conn:
                cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM run_events WHERE run_id = ?",
                                      (self.run_id,)).fetchone()[0]
                cp = checkpoints.save(conn, run_id=self.run_id, phase=phase.value, state=state,
                                      fingerprint=fp.to_dict(), event_cursor=cursor, attempt=self.attempt,
                                      runtime_version=__version__)
        except Exception as exc:
            logger.warning("checkpoint after %s failed for run %s", phase.value, self.run_id, exc_info=True)
            await self._emit("checkpoint_failed", phase=phase.value,
                             message=f"Could not save a checkpoint after {phase.value}: {exc}")
            return
        await self._emit("checkpoint_created", phase=phase.value, message=f"Checkpoint {cp.seq} saved after {phase.value}",
                         payload={"seq": cp.seq, "event_cursor": cp.event_cursor,
                                  "budget": [line.to_dict() for line in self.budget_lines()]})

    async def restore_checkpoint(self, cp: checkpoints.Checkpoint) -> None:
        """Load a checkpoint into this (fresh) machine and rebuild the shadow workspace it described."""
        files = snapshot.restore(self, cp.state)
        self._attempt_clock = time.monotonic()  # wall time accrued before the crash is in the checkpoint
        if files is not None:
            ws = await self._ws()
            await asyncio.to_thread(ws.adopt, {r: v["base"] for r, v in files.items()},
                                    {r: v["current"] for r, v in files.items()})

    async def _skip(self, phase: Phase, message: str) -> None:
        self.phase_statuses[phase] = PhaseStatus.SKIPPED
        await self._emit("phase_started", phase=phase.value, status="running", message=f"Starting {phase.value}")
        await self._emit("phase_skipped", phase=phase.value, status="skipped", message=message)

    async def _run_phase(self, phase: Phase) -> None:
        self.phase_statuses[phase] = PhaseStatus.RUNNING
        self._current_phase = phase.value
        await self._emit("phase_started", phase=phase.value, status="running",
                         message=f"Starting {phase.value}")
        self._update_run_phase(phase.value)

        try:
            handler = getattr(self, f"_phase_{phase.value}", None)
            if handler:
                await handler()
            else:
                await asyncio.sleep(0.1)

            if self.phase_statuses[phase] != PhaseStatus.SKIPPED:
                self.phase_statuses[phase] = PhaseStatus.COMPLETE
                await self._emit("phase_completed", phase=phase.value, status="complete",
                                 message=f"Completed {phase.value}")
        except PhaseBlockedError as e:
            self.phase_statuses[phase] = PhaseStatus.BLOCKED
            await self._emit("phase_blocked", phase=phase.value, status="blocked", message=str(e))
        except Exception as e:
            self.phase_statuses[phase] = PhaseStatus.FAILED
            self.ctx.errors.append(f"{phase.value}: {e}")
            failure = classify(e)
            self._failure = self._failure or failure
            await self._emit("phase_failed", phase=phase.value, status="failed", message=str(e),
                             payload={"failure": failure.to_payload()})

    async def _skip_phase(self, phase: Phase, message: str) -> None:
        """Mark the *current* phase skipped from inside its handler."""
        self.phase_statuses[phase] = PhaseStatus.SKIPPED
        await self._emit("phase_skipped", phase=phase.value, status="skipped", message=message)

    # --------------------------------------------------------------- workspace
    async def _ws(self) -> ShadowWorkspace:
        """Create (once) the shadow workspace all mutation and validation runs inside."""
        if self._workspace is None:
            from patchquest.runtime.workspace import ShadowWorkspace

            ws = ShadowWorkspace(self.run_id, self.ctx.repo_path)
            await asyncio.to_thread(ws.create)
            if self.replay_base:
                await asyncio.to_thread(ws.restore, self.replay_base)
            self._workspace = ws
            self.ctx.workspace_path = str(ws.path)
            await self._emit("workspace_created", message="Created isolated workspace for validation")
        return self._workspace

    # -------------------------------------------------------- command + approval
    async def _request_approval(self, kind: str, command: str | None, reason: str) -> bool:
        from patchquest.orchestrator.approvals import create_approval

        approval_id = await asyncio.to_thread(create_approval, self.run_id, kind, command, reason)
        event = asyncio.Event()
        self._approval_events[approval_id] = event  # registered before announcing, so no lost wake-up
        safe_command = redact_secrets(command) if command else None
        await self._emit("approval_requested", message=reason, payload={
            "approval_id": approval_id, "type": kind, "command": safe_command, "reason": reason})

        timeout = get_config().safety.approval_timeout_seconds
        self._move(RunStatus.WAITING_APPROVAL, f"waiting for a decision: {reason}")
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            approved = self._approval_results.get(approval_id, False) and not self._cancelled.is_set()
        except TimeoutError:
            approved = False
            with get_db() as conn:
                conn.execute("UPDATE approvals SET status = 'expired', resolved_at = ? WHERE id = ? AND status = 'pending'",
                             (now_iso(), approval_id))
            await self._emit("approval_expired", message=f"No response within {timeout}s; treated as denied",
                             payload={"approval_id": approval_id})
        finally:
            self._approval_events.pop(approval_id, None)
            if not self._cancelled.is_set():
                self._move(RunStatus.RUNNING, "decision received")
        self.ctx.approvals.append({"id": approval_id, "type": kind, "command": safe_command, "approved": approved})
        return approved

    async def _exec(self, command: str) -> dict[str, Any]:
        """Run ``command`` in the workspace through the policy gate. Never raises."""
        from patchquest.tools.command_risk import RiskLevel, classify

        self._enforce(budget.BudgetKind.COMMANDS)
        ws = await self._ws()
        decision = classify(command, str(ws.path))
        safe_cmd = redact_secrets(command)
        approved = decision.auto

        if decision.level == RiskLevel.BLOCKED:
            await self._emit("command_blocked", message=f"Blocked: {decision.reason}",
                             payload={"command": safe_cmd, "reason": decision.reason})
            result = {"success": False, "returncode": -2, "stdout": "", "stderr": f"Blocked: {decision.reason}",
                      "blocked": True}
        else:
            if decision.level == RiskLevel.RISKY_ASK:
                approved = await self._request_approval("command", command, decision.reason)
            if not approved:
                await self._emit("command_denied", message="Command not approved", payload={"command": safe_cmd})
                result = {"success": False, "returncode": -3, "stdout": "", "stderr": "Command was not approved",
                          "denied": True}
            else:
                await self._emit("command_started", message=safe_cmd, payload={"command": safe_cmd})
                result = await asyncio.to_thread(self._run_in_runtime, command, str(ws.path), True)
                await self._emit("command_executed", message=f"{safe_cmd} -> exit {result.get('returncode')}", payload={
                    "command": safe_cmd, "returncode": result.get("returncode"), "risk": decision.level.value,
                    "duration_s": result.get("duration_s"), "timed_out": result.get("timed_out", False)})
        entry = {"command": command, **result}
        self.ctx.commands_run.append({"command": command, "result": result})
        return entry

    def _run_in_runtime(self, command: str, cwd: str, approved: bool) -> dict[str, Any]:
        timeout = get_config().safety.max_command_timeout
        if self.ctx.runtime_mode == "docker":
            from patchquest.runtime.docker_runtime import DockerRuntime

            runtime = DockerRuntime(run_id=self.run_id, repo_path=self.ctx.repo_path)
            if not runtime.is_available():
                return {"success": False, "returncode": -1, "stdout": "",
                        "stderr": "Docker runtime requested but Docker is not available", "truncated": False}
            return runtime.run_command(command, cwd, timeout=timeout)
        from patchquest.tools.command_runner import run_command_safe

        return run_command_safe(command, cwd, timeout=timeout, approved=approved)

    async def _run_commands(self, commands: list[str]) -> list[dict[str, Any]]:
        return [await self._exec(cmd) for cmd in commands]

    # ----------------------------------------------------------------- patching
    async def _apply_model_output(self, output: dict[str, Any]) -> tuple[bool, str]:
        """Apply a coder/repair response in the workspace. Returns (applied_anything, error)."""
        from patchquest.patching import (
            EditError,
            apply_changes,
            changes_from_model_output,
            changes_from_unified_diff,
        )
        from patchquest.patching.unified import PatchApplyError

        ws = await self._ws()
        try:
            changes: list[Any] = list(changes_from_model_output(output))
            if not changes and output.get("diff"):
                changes = list(changes_from_unified_diff(output["diff"]))
        except (EditError, PatchApplyError) as exc:
            return False, str(exc)
        if not changes:
            return False, ""

        paths = [getattr(c, "path", None) for c in changes]
        await asyncio.to_thread(ws.note_touched, [p for p in paths if p])
        result = await asyncio.to_thread(apply_changes, str(ws.path), changes)
        if not result.success:
            if result.findings:
                self._patch_secret = True
                self.ctx.secret_findings.extend(result.findings)
                await self._emit("secret_detected", phase="patching", message="Secret detected in proposed patch - blocked")
            return False, result.error
        self.ctx.proposed_diff = await asyncio.to_thread(ws.diff)
        self.ctx.patch_summary = await asyncio.to_thread(ws.summary)
        self.ctx.applied_files = [f["path"] for f in self.ctx.patch_summary]
        return True, ""

    async def _apply_failure_hint(self, error: str) -> str:
        ws = await self._ws()
        blocks = []
        for path in list(self.ctx.selected_context or {})[:4]:
            try:
                text = await asyncio.to_thread((ws.path / path).read_text, "utf-8", "replace")
            except OSError:
                continue
            blocks.append(f'<file path="{path}">\n{text[:6000]}\n</file>')
        return (f"Your previous edit could not be applied: {error}\n"
                "Copy the text to replace EXACTLY from the current file contents below.\n" + "\n".join(blocks))

    # ------------------------------------------------------------------ phases
    async def _phase_intake(self) -> None:
        from patchquest.agents.roles import run_intake_role
        result = await run_intake_role(self.ctx)
        self.ctx.plan = self.ctx.plan or {}
        self.ctx.plan["intake"] = result

    async def _phase_repo_scan(self) -> None:
        from patchquest.memory.repo_indexer import index_repo
        await self._emit("repo_scan_started", phase="repo_scan", message="Scanning repository")
        await asyncio.to_thread(index_repo, self.ctx.repo_path)
        await self._emit("repo_scan_completed", phase="repo_scan", message="Repo scan complete")

    async def _phase_planning(self) -> None:
        from patchquest.agents.roles import run_planner_role
        raw_result = await run_planner_role(self.ctx)
        result = _normalize_plan(raw_result, self.ctx)
        self.ctx.plan = self.ctx.plan or {}
        self.ctx.plan["plan"] = result
        self.ctx.selected_files = result.get("files_to_inspect", [])
        self.ctx.test_commands = [c for c in result.get("test_commands", []) if isinstance(c, str)]
        await self._emit("plan_created", phase="planning", message="Plan created", payload=result)

    async def _phase_research(self) -> None:
        self.phase_statuses[Phase.RESEARCH] = PhaseStatus.SKIPPED
        await self._emit(
            "phase_skipped",
            phase="research",
            status="skipped",
            message="Research not required for this task",
        )

    async def _phase_context_building(self) -> None:
        from patchquest.agents.roles import run_context_builder
        result = await run_context_builder(self.ctx)
        self.ctx.selected_context = result.get("context", {})
        self.ctx.selected_files = result.get("selected_files", self.ctx.selected_files)
        self.ctx.context_provenance = result.get("provenance", [])
        await self._emit("context_selected", phase="context_building",
                         message=f"Selected {len(self.ctx.selected_files)} files",
                         payload={"items": self.ctx.context_provenance})

    async def _phase_analysis(self) -> None:
        if not self.ctx.read_only:
            await self._skip_phase(Phase.ANALYSIS, "Skipped analysis for mutating task")
            return

        from patchquest.agents.roles import run_analysis_role
        analysis = await run_analysis_role(self.ctx)
        self.ctx.analysis = analysis
        await self._emit(
            "analysis_generated",
            phase="analysis",
            message="Read-only analysis generated",
            payload={"analysis": analysis},
        )

    async def _phase_patching(self) -> None:
        if self.ctx.read_only:
            await self._skip_phase(Phase.PATCHING, "Skipped patching for read-only task")
            return

        plan_data = (self.ctx.plan or {}).get("plan", {})
        if isinstance(plan_data, dict) and plan_data.get("expected_patch_scope", "") == "no modifications":
            # The harness classified this task as mutating from the task text itself. A model (small
            # ones misuse this field) does not get to veto that; the coder may still decline explicitly.
            await self._emit("plan_scope_overridden", phase="patching",
                             message="Plan said 'no modifications' but the task requires changes; asking the coder")

        if not isinstance(self.ctx.selected_context, dict):
            self.ctx.selected_context = {}

        from patchquest.agents.roles import run_patch_role
        output = await run_patch_role(self.ctx)
        self.ctx.patch_attempts += 1
        applied, error = await self._apply_model_output(output)
        if not applied and not error and not _says_no_change(output):
            # A mutating task came back with no edits and no explanation. Ask once more, naming the
            # problem, rather than silently ending the run as "no changes".
            await self._emit("patch_empty", phase="patching",
                             message="Model proposed no edits for a mutating task; asking once more")
            output = await run_patch_role(self.ctx, hint=_EMPTY_PATCH_HINT)
            self.ctx.patch_attempts += 1
            applied, error = await self._apply_model_output(output)
            if not applied and not error and not _says_no_change(output):
                self._no_patch = True
                await self._emit("patch_missing", phase="patching",
                                 message="The agent produced no edits for a task that requires changes")
        attempts = max(1, get_config().agent.max_patch_attempts)
        for attempt in range(2, attempts + 1):
            if not error or self._patch_secret:
                break
            # The edit did not apply (e.g. the search text is not in the file). Show the model the
            # error and the file as it really is, then let it try again.
            await self._emit("patch_retry", phase="patching", message=f"Patch did not apply; retry {attempt}/{attempts}",
                             payload={"error": error[:500]})
            output = await run_patch_role(self.ctx, hint=await self._apply_failure_hint(error))
            self.ctx.patch_attempts += 1
            applied, error = await self._apply_model_output(output)
        if error:
            await self._emit("patch_rejected", phase="patching", message=f"Patch rejected: {error}")
            if self._patch_secret:
                return  # a blocked secret is a rejected patch, not a crashed run
            raise PatchQuestError(FailureKind.PATCH_APPLY, f"Failed to apply patch: {error}")
        if not applied:
            return  # model reported nothing to change
        await self._emit("patch_proposed", phase="patching", message="Patch proposed",
                         payload={"files": self.ctx.patch_summary})
        await self._emit("patch_staged", phase="patching",
                         message=f"Patch applied in isolated workspace ({len(self.ctx.applied_files)} files)")

    async def _phase_static_checks(self) -> None:
        if self.ctx.read_only or not self.ctx.proposed_diff:
            await self._skip_phase(Phase.STATIC_CHECKS, "No static checks detected: no changes to check")
            return
        from patchquest.tools.test_runner import detect_check_commands

        ws = await self._ws()
        commands = detect_check_commands(str(ws.path))[: get_config().agent.max_check_commands]
        if not commands:
            await self._skip_phase(Phase.STATIC_CHECKS, "No static checks detected")
            return
        await self._run_commands(commands)

    def _pick_test_commands(self, workspace_path: str) -> list[str]:
        """Planner-named commands only when the policy would run them unattended; else detection."""
        from patchquest.tools.command_risk import classify
        from patchquest.tools.test_runner import detect_test_commands

        limit = get_config().agent.max_test_commands
        planned = [c for c in self.ctx.test_commands if classify(c, workspace_path).auto and _tool_available(c)]
        detected = [c for c in detect_test_commands(workspace_path) if _tool_available(c)]
        return (planned or detected)[:limit]

    async def _baseline(self, commands: list[str]) -> list[dict[str, Any]]:
        """Run the same commands on the pristine workspace state, then put the patch back."""
        ws = await self._ws()
        patched = await asyncio.to_thread(ws.checkpoint)
        await asyncio.to_thread(ws.restore_base)
        await self._emit("baseline_started", phase="testing", message="Re-running failing checks without the patch")
        try:
            return await self._run_commands(commands)
        finally:
            await asyncio.to_thread(ws.restore, patched)

    async def _phase_testing(self) -> None:
        if self.ctx.read_only or not self.ctx.proposed_diff:
            await self._skip_phase(Phase.TESTING, "No changes to validate")
            return

        from patchquest.validation import classify_failures

        ws = await self._ws()
        commands = self._pick_test_commands(str(ws.path))
        if not commands:
            self.ctx.verdict = "no_tests"
            await self._skip_phase(Phase.TESTING, "No test commands detected")
            return

        await self._emit("tests_started", phase="testing", message="Running tests")
        results = await self._run_commands(commands)
        self.ctx.test_results.extend(results)
        failing = [r for r in results if not r["success"]]

        cfg = get_config().agent
        classification: dict[str, dict[str, list[str]]] = {}
        baseline_done = False
        while failing:
            if not baseline_done:  # attribute failures once, on the pristine state
                baseline_done = True
                self.ctx.baseline_results = await self._baseline(commands)
            classification = classify_failures(failing, self.ctx.baseline_results)
            for r in failing:
                r["classification"] = classification.get(r["command"], {})
            if self.ctx.repair_rounds >= cfg.max_repair_rounds:
                break

            self.ctx.repair_rounds += 1
            await self._emit("repair_started", phase="testing",
                             message=f"Repair attempt {self.ctx.repair_rounds}/{cfg.max_repair_rounds}",
                             payload={"classification": classification})
            from patchquest.agents.roles import run_repair_role

            output = await run_repair_role(self.ctx, failing, str(ws.path), self.ctx.repair_rounds)
            applied, error = await self._apply_model_output(output)
            if error or not applied:
                await self._emit("patch_rejected", phase="testing", message=f"Repair not applied: {error or 'no change proposed'}")
                break
            results = await self._run_commands(commands)
            self.ctx.test_results.extend(results)
            failing = [r for r in results if not r["success"]]

        if not failing:
            self.ctx.verdict = "passed"
        else:
            classification = classify_failures(failing, self.ctx.baseline_results)
            regressed = any(c["new"] for c in classification.values())
            self.ctx.verdict = "regression" if regressed else "unresolved"
        await self._emit("tests_completed", phase="testing",
                         message=f"Validation verdict: {self.ctx.verdict}",
                         payload={"verdict": self.ctx.verdict, "repair_rounds": self.ctx.repair_rounds,
                                  "classification": classification})

    async def _phase_review(self) -> None:
        if self.ctx.read_only or not self.ctx.proposed_diff:
            await self._skip_phase(Phase.REVIEW, "Skipped review — no patch to review")
            return

        from patchquest.agents.roles import run_reviewer_role
        result = await run_reviewer_role(self.ctx)
        self.ctx.review = result
        self.ctx.plan = self.ctx.plan or {}
        self.ctx.plan["review"] = result

    async def _phase_security_scan(self) -> None:
        from patchquest.tools.secret_guard import scan_text
        ctx_values = self.ctx.selected_context.values() if isinstance(self.ctx.selected_context, dict) else []
        all_text = "\n".join(str(v) for v in ctx_values)
        findings = []
        if self.ctx.proposed_diff:
            added = "\n".join(ln[1:] for ln in self.ctx.proposed_diff.split("\n")
                              if ln.startswith("+") and not ln.startswith("+++"))
            findings = scan_text(added)
            if findings:
                self._patch_secret = True
        findings += scan_text(all_text)
        self.ctx.secret_findings.extend(findings)
        await self._emit("security_scan_completed", phase="security_scan",
                         message=f"Security scan: {len(findings)} findings",
                         payload={"findings_count": len(findings)})

    # ------------------------------------------------------------ finalization
    async def _finalize_patch(self) -> None:
        """Decide what happens to the validated change and set ``ctx.outcome``."""
        ctx = self.ctx
        if ctx.read_only:
            ctx.outcome = "read_only"
            return
        ws = self._workspace
        if self._patch_secret:
            ctx.outcome = "rejected"
            await self._emit("patch_rejected", phase="final_report", message="Patch rejected: it introduces a secret")
            return
        if self._no_patch:
            ctx.outcome = "no_patch"
            return
        if ws is None or not ctx.proposed_diff or not ws.summary():
            ctx.outcome = "no_changes"
            return

        policy = get_config().agent.promote_policy
        rejected_by_review = isinstance(ctx.review, dict) and str(ctx.review.get("recommendation", "")).lower() in (
            "reject", "request_changes", "block")
        promotable_verdicts = _PROMOTABLE_VERDICTS | ({"unresolved"} if policy == "on_no_regression" else set())
        promotable = ctx.verdict in promotable_verdicts and not rejected_by_review

        if policy == "never":
            ctx.outcome = "rejected"
            await self._emit("patch_rejected", phase="final_report",
                             message="promote_policy=never: diff left for review (not applied)")
            return
        if policy != "always" and not promotable:
            why = f"validation verdict '{ctx.verdict}'" + (" and reviewer recommended changes" if rejected_by_review else "")
            if not await self._request_approval("promote_patch", None, f"Apply patch to the repository despite {why}?"):
                ctx.outcome = "rejected"
                await self._emit("patch_rejected", phase="final_report", message=f"Patch not applied ({why})")
                return

        if self._promotion_reconciled == "applied":  # a previous attempt wrote the files, then died
            ctx.outcome = "applied"
            ctx.applied_files = [s["path"] for s in ws.summary()] or list(ctx.applied_files)
            await self._emit("promotion_reconciled", phase="final_report",
                             message="The patch had already been applied before the interruption")
            return
        manifest = await asyncio.to_thread(ws.promotion_manifest)
        # Journal intent *before* touching the real repository: a crash between here and the
        # completion event is then recognisable, and resume can verify what actually landed.
        await self._emit("promotion_started", phase="final_report", payload={"files": manifest})
        result = await asyncio.to_thread(ws.promote)
        if not result.success:
            await self._emit("promotion_failed", phase="final_report", message=str(result.error))
            ctx.outcome = "conflict"
            ctx.errors.append(f"promote: {result.error}")
            await self._emit("patch_rejected", phase="final_report", message=f"Could not apply to repository: {result.error}")
            return
        ctx.outcome = "applied"
        ctx.applied_files = result.files_changed
        await self._emit("promotion_completed", phase="final_report", payload={"files": list(manifest)})
        await self._emit("patch_applied", phase="final_report",
                         message=f"Patch applied to {len(ctx.applied_files)} files",
                         payload={"files": result.files_changed})

    async def _phase_final_report(self) -> None:
        await self._finalize_patch()
        from patchquest.reports.final_report import generate_report
        report = generate_report(self.ctx)
        with get_db() as conn:
            conn.execute(
                """INSERT INTO reports (run_id, report_md, diff_patch, commands_log, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (self.run_id, report["report_md"], report.get("diff_patch"),
                 report.get("commands_log"), now_iso()),
            )
        await self._emit("report_generated", phase="final_report", message="Final report generated")

    # ------------------------------------------------------------- run lifecycle
    async def _complete_run(self) -> None:
        self._move(RunStatus.COMPLETED, "all phases finished",
                   fields={"outcome": self.ctx.outcome, "verdict": self.ctx.verdict})
        await self._emit("run_completed", message="Run completed successfully",
                         payload={"outcome": self.ctx.outcome, "verdict": self.ctx.verdict})

    async def _fail_run(self, reason: str, status: str = "failed") -> None:
        try:
            from patchquest.reports.final_report import generate_report
            report = generate_report(self.ctx)
            with get_db() as conn:
                existing = conn.execute(
                    "SELECT id FROM reports WHERE run_id = ?", (self.run_id,)
                ).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT INTO reports (run_id, report_md, diff_patch, commands_log, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (self.run_id, report["report_md"], report.get("diff_patch"),
                         report.get("commands_log"), now_iso()),
                    )
        except Exception as report_exc:
            logger.warning("Failed to generate report for failed run %s: %s", self.run_id, report_exc)

        failure = (Failure(FailureKind.USER_CANCELLED, reason) if status == "cancelled"
                   else self._failure or Failure(FailureKind.INTERNAL_INVARIANT, reason))
        self._move(RunStatus(status), reason, fields={
            "outcome": self.ctx.outcome or "rejected", "verdict": self.ctx.verdict, "failure_kind": failure.kind.value})
        await self._emit("run_failed", message=f"Run failed: {reason}", payload={"failure": failure.to_payload()})

    def _update_run_phase(self, phase: str) -> None:
        with get_db() as conn:
            conn.execute(
                "UPDATE runs SET current_phase = ?, updated_at = ? WHERE id = ?",
                (phase, now_iso(), self.run_id),
            )

    async def _emit(self, event_type: str, phase: str | None = None,
                    status: str | None = None, message: str | None = None,
                    payload: dict[str, Any] | None = None) -> None:
        with get_db() as conn:
            event_id, event_uid = ledger.append(
                conn, self.run_id, event_type, phase=phase, status=status, message=message, payload=payload,
                attempt=self.attempt, correlation_id=self.correlation_id, causation_id=self._phase_event_uid)
        if event_type == "phase_started":
            self._phase_event_uid = event_uid

        event = {
            "id": event_id,
            "event_uid": event_uid,
            "type": event_type,
            "run_id": self.run_id,
            "phase": phase,
            "status": status,
            "message": message,
            "payload": payload,
        }
        await event_bus.emit(self.run_id, event)

    async def resolve_approval(self, approval_id: str, approved: bool) -> None:
        self._approval_results[approval_id] = approved
        evt = self._approval_events.get(approval_id)
        if evt:
            evt.set()

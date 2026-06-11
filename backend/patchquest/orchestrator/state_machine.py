"""Deterministic state machine for orchestrating a coding run."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any

from patchquest.database import get_db, insert_event, now_iso
from patchquest.orchestrator.event_bus import event_bus
from patchquest.orchestrator.phases import PHASE_ORDER, Phase, PhaseStatus
from patchquest.orchestrator.run_context import RunContext

logger = logging.getLogger(__name__)

_PLAN_REQUIRED_KEYS = {"plan", "files_to_inspect", "tests_likely_needed",
                       "expected_patch_scope", "stop_conditions", "test_commands"}


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
    ) -> None:
        from patchquest.orchestrator.run_context import _detect_read_only

        self.run_id = run_id
        read_only = _detect_read_only(task, dry_run)
        self.ctx = RunContext(
            run_id=run_id, repo_path=repo_path, task=task,
            provider=provider, model=model, runtime_mode=runtime_mode,
            dry_run=dry_run, read_only=read_only,
        )
        self.phase_statuses: dict[Phase, PhaseStatus] = {p: PhaseStatus.PENDING for p in Phase}
        self._approval_events: dict[str, asyncio.Event] = {}
        self._approval_results: dict[str, bool] = {}

    async def execute(self) -> None:
        try:
            for phase in PHASE_ORDER:
                await self._run_phase(phase)
                if self.phase_statuses[phase] == PhaseStatus.FAILED:
                    await self._fail_run(f"Phase {phase.value} failed")
                    return

            await self._complete_run()
        except Exception as e:
            logger.error(f"Run {self.run_id} crashed: {e}\n{traceback.format_exc()}")
            await self._fail_run(str(e))

    async def _run_phase(self, phase: Phase) -> None:
        self.phase_statuses[phase] = PhaseStatus.RUNNING
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
            await self._emit("phase_failed", phase=phase.value, status="failed", message=str(e))

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
        self.ctx.test_commands = result.get("test_commands", [])
        await self._emit("plan_created", phase="planning", message="Plan created",
                         payload=result)

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
        await self._emit("context_selected", phase="context_building",
                         message=f"Selected {len(self.ctx.selected_files)} files")

    async def _phase_analysis(self) -> None:
        if not self.ctx.read_only:
            self.phase_statuses[Phase.ANALYSIS] = PhaseStatus.SKIPPED
            await self._emit(
                "phase_skipped",
                phase="analysis",
                status="skipped",
                message="Skipped analysis for mutating task",
            )
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
            self.phase_statuses[Phase.PATCHING] = PhaseStatus.SKIPPED
            await self._emit("phase_skipped", phase="patching", status="skipped",
                             message="Skipped patching for read-only task")
            return

        plan_data = (self.ctx.plan or {}).get("plan", {})
        if isinstance(plan_data, dict):
            scope = plan_data.get("expected_patch_scope", "")
            if scope == "no modifications":
                self.phase_statuses[Phase.PATCHING] = PhaseStatus.SKIPPED
                await self._emit("phase_skipped", phase="patching", status="skipped",
                                 message="Skipped patching — plan indicates no modifications")
                return

        if not isinstance(self.ctx.selected_context, dict):
            self.ctx.selected_context = {}

        from patchquest.agents.roles import run_patch_role
        result = await run_patch_role(self.ctx)
        if result.get("diff"):
            self.ctx.proposed_diff = result["diff"]
            await self._emit("patch_proposed", phase="patching", message="Patch proposed")

            from patchquest.tools.secret_guard import scan_text
            findings = scan_text(result["diff"])
            if findings:
                self.ctx.secret_findings.extend(findings)
                await self._emit("secret_detected", phase="patching",
                                 message="Secret detected in proposed patch - blocked")
                await self._emit("patch_rejected", phase="patching",
                                 message="Patch rejected due to secret detection")
                return

            from patchquest.tools.patch_tools import apply_unified_diff
            apply_result = apply_unified_diff(result["diff"], self.ctx.repo_path)
            if not apply_result.get("success"):
                raise RuntimeError(
                    f"Failed to apply patch: {apply_result.get('error', 'unknown error')}"
                )

            self.ctx.applied_files = apply_result.get("files_changed", [])
            await self._emit("patch_applied", phase="patching",
                             message=f"Patch applied to {len(self.ctx.applied_files)} files")

    async def _phase_static_checks(self) -> None:
        from patchquest.tools.test_runner import detect_check_commands
        commands = detect_check_commands(self.ctx.repo_path)
        if not commands:
            self.phase_statuses[Phase.STATIC_CHECKS] = PhaseStatus.SKIPPED
            await self._emit(
                "phase_skipped",
                phase="static_checks",
                status="skipped",
                message="No static checks detected",
            )
            return
        from patchquest.tools.command_runner import run_command_safe
        cmd = commands[0]
        result = await asyncio.to_thread(run_command_safe, cmd, self.ctx.repo_path)
        self.ctx.commands_run.append({"command": cmd, "result": result})

    async def _phase_testing(self) -> None:
        from patchquest.tools.test_runner import detect_test_commands
        commands = self.ctx.test_commands or detect_test_commands(self.ctx.repo_path)
        if not commands:
            self.phase_statuses[Phase.TESTING] = PhaseStatus.SKIPPED
            await self._emit(
                "phase_skipped",
                phase="testing",
                status="skipped",
                message="No test commands detected",
            )
            return

        await self._emit("tests_started", phase="testing", message="Running tests")
        from patchquest.tools.command_runner import run_command_safe
        for cmd in commands[:2]:
            result = await asyncio.to_thread(run_command_safe, cmd, self.ctx.repo_path)
            self.ctx.test_results.append({"command": cmd, **result})
            self.ctx.commands_run.append({"command": cmd, "result": result})

        await self._emit("tests_completed", phase="testing",
                         message=f"Ran {len(self.ctx.test_results)} test commands")

    async def _phase_review(self) -> None:
        if self.ctx.read_only or not self.ctx.proposed_diff:
            self.phase_statuses[Phase.REVIEW] = PhaseStatus.SKIPPED
            await self._emit("phase_skipped", phase="review", status="skipped",
                             message="Skipped review — no patch to review")
            return

        from patchquest.agents.roles import run_reviewer_role
        result = await run_reviewer_role(self.ctx)
        self.ctx.plan = self.ctx.plan or {}
        self.ctx.plan["review"] = result

    async def _phase_security_scan(self) -> None:
        from patchquest.tools.secret_guard import scan_text
        ctx_values = self.ctx.selected_context.values() if isinstance(self.ctx.selected_context, dict) else []
        all_text = "\n".join(str(v) for v in ctx_values)
        if self.ctx.proposed_diff:
            all_text += "\n" + self.ctx.proposed_diff
        findings = scan_text(all_text)
        self.ctx.secret_findings.extend(findings)
        await self._emit("security_scan_completed", phase="security_scan",
                         message=f"Security scan: {len(findings)} findings",
                         payload={"findings_count": len(findings)})

    async def _phase_final_report(self) -> None:
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

    async def _complete_run(self) -> None:
        with get_db() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                ("completed", now_iso(), now_iso(), self.run_id),
            )
        await self._emit("run_completed", message="Run completed successfully")

    async def _fail_run(self, reason: str) -> None:
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

        with get_db() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                ("failed", now_iso(), now_iso(), self.run_id),
            )
        await self._emit("run_failed", message=f"Run failed: {reason}")

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
            insert_event(conn, self.run_id, event_type, phase, status, message, payload)

        event = {
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


class PhaseBlockedError(Exception):
    pass

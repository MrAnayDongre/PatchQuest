"""Tests for orchestration hardening: read-only routing, plan normalization,
patching safety, failed-run reports."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from patchquest.config import AppConfig, set_config
from patchquest.database import get_db, init_db, insert_event, now_iso, set_db_path
from patchquest.orchestrator.phases import Phase, PhaseStatus
from patchquest.orchestrator.run_context import RunContext, _detect_read_only
from patchquest.orchestrator.state_machine import (
    RunStateMachine,
    _ensure_list,
    _normalize_plan,
)
from patchquest.reports.final_report import generate_report, generate_report_from_run_record


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    init_db()
    set_config(AppConfig())
    yield


# ============================================================
# Read-only task detection
# ============================================================

class TestReadOnlyDetection:
    def test_do_not_modify_files(self):
        assert _detect_read_only("Give a 5 bullet architecture summary. Do not modify files.", False)

    def test_do_not_make_code_changes(self):
        assert _detect_read_only("Analyze repo. Do not make code changes.", False)

    def test_inspect(self):
        assert _detect_read_only("Inspect the login module", False)

    def test_analyze(self):
        assert _detect_read_only("Analyze the database layer", False)

    def test_summarize(self):
        assert _detect_read_only("Summarize the codebase architecture", False)

    def test_explain(self):
        assert _detect_read_only("Explain how the auth system works", False)

    def test_architecture_summary(self):
        assert _detect_read_only("Give an architecture summary of the project", False)

    def test_report_only(self):
        assert _detect_read_only("Report only on code quality", False)

    def test_dry_run_flag(self):
        assert _detect_read_only("Fix the bug in login.py", True)

    def test_normal_task_not_read_only(self):
        assert not _detect_read_only("Fix the off-by-one error in pagination", False)

    def test_add_tests_not_read_only(self):
        assert not _detect_read_only("Add unit tests for UserService", False)

    def test_context_defaults(self):
        ctx = RunContext(run_id="r", repo_path="/tmp", task="Summarize repo")
        assert ctx.read_only is False  # must be set explicitly

    def test_state_machine_sets_read_only(self):
        sm = RunStateMachine("r1", "/tmp", "Summarize the repo. Do not modify files.")
        assert sm.ctx.read_only is True

    def test_state_machine_dry_run(self):
        sm = RunStateMachine("r1", "/tmp", "Fix the bug", dry_run=True)
        assert sm.ctx.read_only is True

    def test_state_machine_normal_task(self):
        sm = RunStateMachine("r1", "/tmp", "Fix the bug in login.py")
        assert sm.ctx.read_only is False

    def test_modify_readme_with_other_files_constraint_is_mutating(self):
        task = (
            'Modify README.md by adding exactly this one sentence somewhere appropriate: '
            '"Docker NVIDIA mutation integration." Keep the change minimal. '
            "Do not modify any other files."
        )
        assert not _detect_read_only(task, False)

    def test_update_readme_minimal_is_mutating(self):
        assert not _detect_read_only(
            "Update README.md with one sentence. Keep the change minimal.", False
        )

    def test_add_exactly_this_sentence_is_mutating(self):
        assert not _detect_read_only(
            'Add exactly this sentence to README.md: "PatchQuest integration marker."', False
        )

    def test_fix_typo_minimal_is_mutating(self):
        assert not _detect_read_only(
            "Fix the typo in README. Keep the change minimal.", False
        )

    def test_add_unit_test_is_mutating(self):
        assert not _detect_read_only("Add a unit test for scheduler.", False)

    def test_create_docs_no_code_change_is_mutating(self):
        assert not _detect_read_only(
            "Create a docs section, but don't change code files.", False
        )

    def test_refactor_without_behavior_change_is_mutating(self):
        assert not _detect_read_only(
            "Refactor this function without changing behavior.", False
        )

    def test_analyze_suggest_no_edit_is_read_only(self):
        assert _detect_read_only(
            "Analyze README.md and suggest changes, do not edit", False
        )

    def test_propose_patch_without_applying_is_read_only(self):
        assert _detect_read_only(
            "Review the repo and propose a patch without applying it", False
        )

    def test_inspect_and_summarize_is_read_only(self):
        assert _detect_read_only(
            "Inspect this repo and summarize it. Do not modify files.", False
        )

    def test_analyze_architecture_bullets_is_read_only(self):
        assert _detect_read_only(
            "Analyze the architecture and give me 3 bullets.", False
        )

    def test_explain_scheduler_is_read_only(self):
        assert _detect_read_only("Explain how the scheduler works.", False)

    def test_review_diff_risks_is_read_only(self):
        assert _detect_read_only("Review the diff and tell me risks.", False)

    def test_state_machine_readme_mutation_not_read_only(self):
        task = (
            'Modify README.md by adding exactly this one sentence somewhere appropriate: '
            '"Docker NVIDIA mutation integration." Do not modify any other files.'
        )
        sm = RunStateMachine("mut1", "/tmp", task)
        assert sm.ctx.read_only is False


# ============================================================
# Plan normalization
# ============================================================

class TestPlanNormalization:
    def test_valid_json_passes_through(self):
        ctx = RunContext(run_id="r", repo_path="/tmp", task="fix bug")
        raw = {
            "plan": "Fix the bug",
            "files_to_inspect": ["src/main.py"],
            "tests_likely_needed": ["test_main"],
            "expected_patch_scope": "1 file",
            "stop_conditions": ["tests pass"],
            "test_commands": ["pytest"],
        }
        result = _normalize_plan(raw, ctx)
        assert result["plan"] == "Fix the bug"
        assert result["files_to_inspect"] == ["src/main.py"]
        assert result["test_commands"] == ["pytest"]

    def test_parse_error_creates_fallback(self):
        ctx = RunContext(run_id="r", repo_path="/tmp", task="fix bug")
        raw = {"raw_response": "Here are some bullet points...", "parse_error": True}
        result = _normalize_plan(raw, ctx)
        assert isinstance(result["plan"], str)
        assert result["files_to_inspect"] == []
        assert result["test_commands"] == []
        assert result.get("parse_error") is True
        assert result["expected_patch_scope"] == "unknown"

    def test_parse_error_read_only(self):
        ctx = RunContext(run_id="r", repo_path="/tmp", task="summarize", read_only=True)
        raw = {"raw_response": "Architecture overview...", "parse_error": True}
        result = _normalize_plan(raw, ctx)
        assert result["expected_patch_scope"] == "no modifications"
        assert "read-only" in result["stop_conditions"][0]

    def test_missing_keys_filled_with_defaults(self):
        ctx = RunContext(run_id="r", repo_path="/tmp", task="fix bug")
        raw = {"plan": "Do something"}
        result = _normalize_plan(raw, ctx)
        assert result["plan"] == "Do something"
        assert result["files_to_inspect"] == []
        assert result["test_commands"] == []

    def test_string_values_wrapped_in_list(self):
        ctx = RunContext(run_id="r", repo_path="/tmp", task="fix bug")
        raw = {"plan": "p", "files_to_inspect": "single_file.py"}
        result = _normalize_plan(raw, ctx)
        assert result["files_to_inspect"] == ["single_file.py"]

    def test_downstream_never_gets_raw_string(self):
        ctx = RunContext(run_id="r", repo_path="/tmp", task="fix bug")
        raw = {"raw_response": "free form text", "parse_error": True}
        result = _normalize_plan(raw, ctx)
        assert isinstance(result, dict)
        assert isinstance(result["plan"], str)
        assert isinstance(result["files_to_inspect"], list)
        assert isinstance(result["test_commands"], list)


class TestEnsureList:
    def test_list_passthrough(self):
        assert _ensure_list(["a", "b"]) == ["a", "b"]

    def test_none_returns_empty(self):
        assert _ensure_list(None) == []

    def test_string_wraps(self):
        assert _ensure_list("foo") == ["foo"]

    def test_empty_string_returns_empty(self):
        assert _ensure_list("") == []

    def test_number_wraps(self):
        assert _ensure_list(42) == ["42"]


# ============================================================
# Patching phase hardening
# ============================================================

def _insert_run(run_id: str) -> None:
    """Insert a stub run record so FK constraints pass for events."""
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO runs (id, repo_path, task, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, "/tmp", "task", "created", now, now),
        )


class TestPatchingHardening:
    @pytest.mark.asyncio
    async def test_read_only_skips_patching(self):
        _insert_run("r1")
        sm = RunStateMachine("r1", "/tmp", "Summarize architecture. Do not modify files.")
        assert sm.ctx.read_only is True
        await sm._phase_patching()
        assert sm.phase_statuses[Phase.PATCHING] == PhaseStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_no_modifications_plan_skips_patching(self):
        _insert_run("r2")
        sm = RunStateMachine("r2", "/tmp", "Fix the bug")
        sm.ctx.read_only = False
        sm.ctx.plan = {"plan": {"expected_patch_scope": "no modifications"}}
        await sm._phase_patching()
        assert sm.phase_statuses[Phase.PATCHING] == PhaseStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_review_skipped_when_no_diff(self):
        _insert_run("r3")
        sm = RunStateMachine("r3", "/tmp", "Analyze repo. Do not modify files.")
        sm.ctx.proposed_diff = None
        await sm._phase_review()
        assert sm.phase_statuses[Phase.REVIEW] == PhaseStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_patching_with_string_context_does_not_crash(self):
        """The .items() crash that was observed in production."""
        _insert_run("r4")
        sm = RunStateMachine("r4", "/tmp", "Fix bug")
        sm.ctx.read_only = False
        sm.ctx.selected_context = "this is a string, not a dict"  # type: ignore
        sm.ctx.plan = {"plan": {"expected_patch_scope": "1 file"}}
        await sm._phase_patching()


# ============================================================
# Read-only run completes with final report
# ============================================================

class TestReadOnlyRunCompletion:
    @pytest.mark.asyncio
    async def test_read_only_run_completes(self):
        _insert_run("ro-run")
        sm = RunStateMachine("ro-run", "/tmp", "Summarize. Do not modify files.")
        await sm.execute()
        with get_db() as conn:
            run = conn.execute("SELECT status FROM runs WHERE id = 'ro-run'").fetchone()
        assert run is not None
        assert run["status"] == "completed"

    @pytest.mark.asyncio
    async def test_read_only_run_generates_report(self):
        _insert_run("ro-rpt")
        sm = RunStateMachine("ro-rpt", "/tmp", "Analyze. Do not modify files.")
        await sm.execute()
        with get_db() as conn:
            report = conn.execute("SELECT * FROM reports WHERE run_id = 'ro-rpt'").fetchone()
        assert report is not None
        assert "Read-only" in report["report_md"]

    @pytest.mark.asyncio
    async def test_dry_run_skips_patching(self):
        _insert_run("dry1")
        sm = RunStateMachine("dry1", "/tmp", "Fix the bug", dry_run=True)
        await sm.execute()
        assert sm.phase_statuses[Phase.PATCHING] == PhaseStatus.SKIPPED


# ============================================================
# Failed run report
# ============================================================

class TestFailedRunReport:
    @pytest.mark.asyncio
    async def test_failed_run_has_report(self):
        """When a run fails, _fail_run should still generate a report."""
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO runs (id, repo_path, task, status, provider, model,
                   runtime_mode, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("fail1", "/tmp", "task", "created", "nvidia", "openai/gpt-oss-120b",
                 "local", now, now),
            )

        sm = RunStateMachine(
            "fail1", "/tmp", "task",
            provider="nvidia", model="openai/gpt-oss-120b",
        )
        sm.ctx.errors.append("patching: 'str' object has no attribute 'items'")
        await sm._fail_run("Phase patching failed")

        with get_db() as conn:
            report = conn.execute("SELECT * FROM reports WHERE run_id = 'fail1'").fetchone()
        assert report is not None
        assert "nvidia" in report["report_md"].lower() or "**Provider:** nvidia" in report["report_md"]

    def test_generate_report_from_run_record(self):
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO runs (id, repo_path, task, status, provider, model,
                   runtime_mode, created_at, updated_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("fail2", "/tmp", "task", "failed", "nvidia", "openai/gpt-oss-120b",
                 "local", now, now, now),
            )
            insert_event(conn, "fail2", "phase_failed", phase="patching", status="failed",
                         message="'str' object has no attribute 'items'")
            insert_event(conn, "fail2", "phase_completed", phase="planning", status="complete",
                         message="Completed planning")

        report = generate_report_from_run_record("fail2")
        assert "**Provider:** nvidia" in report["report_md"]
        assert "**Model:** openai/gpt-oss-120b" in report["report_md"]
        assert "patching" in report["report_md"]
        assert "mock" not in report["report_md"].lower() or "mock" not in report["report_md"]

    def test_report_endpoint_returns_for_failed_run(self):
        """The /api/reports endpoint should not 404 for failed runs."""
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO runs (id, repo_path, task, status, provider, model,
                   runtime_mode, created_at, updated_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("fail3", "/tmp", "task", "failed", "nvidia", "openai/gpt-oss-120b",
                 "local", now, now, now),
            )
            insert_event(conn, "fail3", "phase_failed", phase="patching", status="failed",
                         message="Some error")

        report = generate_report_from_run_record("fail3")
        assert report["report_md"] is not None
        assert "Failed" in report["report_md"]


# ============================================================
# Final report content for real providers
# ============================================================

class TestFinalReportContent:
    def test_nvidia_report_no_mock_limitation(self):
        ctx = RunContext(
            run_id="r1", repo_path="/tmp", task="analyze",
            provider="nvidia", model="openai/gpt-oss-120b",
            read_only=True,
        )
        report = generate_report(ctx)
        assert "**Provider:** nvidia" in report["report_md"]
        assert "**Model:** openai/gpt-oss-120b" in report["report_md"]
        assert "Read-only" in report["report_md"]
        assert "mock" not in report["report_md"].lower()
        assert "Limitations" not in report["report_md"]

    def test_failed_report_includes_errors(self):
        ctx = RunContext(
            run_id="r2", repo_path="/tmp", task="fix",
            provider="nvidia", model="openai/gpt-oss-120b",
        )
        ctx.errors.append("patching: something broke")
        report = generate_report(ctx)
        assert "## Errors" in report["report_md"]
        assert "something broke" in report["report_md"]
        assert "Failed" in report["report_md"]


# ============================================================
# NVIDIA integration - free-form planning does not crash
# ============================================================

class TestNvidiaFreeFormPlanning:
    @pytest.mark.asyncio
    async def test_freeform_planning_normalizes(self):
        """Simulates NVIDIA returning markdown instead of JSON for planning."""
        ctx = RunContext(
            run_id="nv1", repo_path="/tmp",
            task="Give a 5 bullet architecture summary. Do not modify files.",
            provider="nvidia", model="openai/gpt-oss-120b",
            read_only=True,
        )
        raw = {"raw_response": "- Point 1\n- Point 2\n- Point 3", "parse_error": True}
        result = _normalize_plan(raw, ctx)
        assert result["expected_patch_scope"] == "no modifications"
        assert isinstance(result["files_to_inspect"], list)
        assert result.get("parse_error") is True

    @pytest.mark.asyncio
    async def test_freeform_planning_does_not_crash_state_machine(self):
        _insert_run("nv2")
        sm = RunStateMachine(
            "nv2", "/tmp",
            "Summarize architecture. Do not modify files.",
            provider="mock", model="mock-planner",
        )
        await sm.execute()
        with get_db() as conn:
            run = conn.execute("SELECT status FROM runs WHERE id = 'nv2'").fetchone()
        assert run["status"] == "completed"

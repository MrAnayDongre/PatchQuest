"""Read-only analysis capture and final-report generation.

Invariants: an analysis-only run stores its analysis and still produces a report; mutating runs are unaffected;
reports redact secrets and say plainly when nothing was changed or executed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from patchquest.agents.provider_base import ProviderResponse
from patchquest.agents.providers_nvidia import NvidiaProvider, _extract_output_text
from patchquest.database import get_db, insert_event, now_iso
from patchquest.orchestrator.phases import Phase, PhaseStatus
from patchquest.orchestrator.run_context import RunContext
from patchquest.orchestrator.state_machine import RunStateMachine
from patchquest.reports.final_report import generate_report, generate_report_from_run_record
from tests.support import TEST_REPO, insert_run

# ======================================================================
# Read-only analysis
# ======================================================================

class TestReadOnlyAnalysisCapture:
    @pytest.mark.asyncio
    async def test_read_only_run_stores_analysis(self):
        insert_run("ro-analysis", "Summarize repo. Do not modify files.")
        sm = RunStateMachine("ro-analysis", TEST_REPO, "Summarize repo. Do not modify files.")
        await sm.execute()
        assert sm.ctx.analysis is not None
        assert "Hello!" in sm.ctx.analysis
        assert "high-level summary" in sm.ctx.analysis.lower()

    @pytest.mark.asyncio
    async def test_analysis_phase_runs_for_read_only(self):
        insert_run("ro-phase")
        sm = RunStateMachine("ro-phase", TEST_REPO, "Analyze repo. Do not modify files.")
        await sm.execute()
        assert sm.phase_statuses[Phase.ANALYSIS] == PhaseStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_analysis_skipped_for_non_read_only(self):
        insert_run("rw-phase")
        sm = RunStateMachine("rw-phase", TEST_REPO, "Fix the off-by-one error in pagination")
        await sm.execute()
        assert sm.phase_statuses[Phase.ANALYSIS] == PhaseStatus.SKIPPED
        assert sm.ctx.analysis is None

    @pytest.mark.asyncio
    async def test_analysis_generated_event_emitted(self):
        insert_run("ro-event")
        sm = RunStateMachine("ro-event", TEST_REPO, "Summarize. Do not modify files.")
        await sm.execute()
        with get_db() as conn:
            row = conn.execute(
                "SELECT payload_json FROM run_events WHERE run_id = ? AND type = 'analysis_generated'",
                ("ro-event",),
            ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        assert "analysis" in payload
        assert payload["analysis"]


class TestFinalReportAnalysis:
    def test_report_includes_analysis_section(self):
        ctx = RunContext(
            run_id="r1",
            repo_path="/tmp",
            task="Say hello and summarize. Do not modify files.",
            provider="nvidia",
            model="openai/gpt-oss-120b",
            read_only=True,
            analysis="Hello!\n\n- Bullet one\n- Bullet two\n- Bullet three",
        )
        report = generate_report(ctx)
        assert "## Analysis" in report["report_md"]
        assert "Hello!" in report["report_md"]
        assert "Bullet one" in report["report_md"]

    def test_report_does_not_include_reasoning_text(self):
        ctx = RunContext(
            run_id="r2",
            repo_path="/tmp",
            task="Summarize. Do not modify files.",
            provider="nvidia",
            model="openai/gpt-oss-120b",
            read_only=True,
            analysis="Hello!\n\n- One\n- Two\n- Three",
        )
        report = generate_report(ctx)
        assert "reasoning_text" not in report["report_md"]
        assert "chain-of-thought" not in report["report_md"].lower()

    def test_mock_read_only_run_includes_analysis_in_report(self):
        ctx = RunContext(
            run_id="mock-ro",
            repo_path="/tmp",
            task="Give a summary. Do not modify files.",
            provider="mock",
            read_only=True,
            analysis=(
                "Hello! Here is a high-level summary of this repository:\n\n"
                "- Backend FastAPI orchestrator with phase-based run execution\n"
                "- React frontend for launching and monitoring coding runs\n"
                "- SQLite persistence for runs, events, and reports"
            ),
        )
        report = generate_report(ctx)
        assert "## Analysis" in report["report_md"]
        assert "Hello!" in report["report_md"]
        assert "Limitations" in report["report_md"]

    @pytest.mark.asyncio
    async def test_end_to_end_read_only_report_has_analysis(self):
        insert_run("ro-report", "Say hello. Do not modify files.")
        sm = RunStateMachine("ro-report", TEST_REPO, "Say hello. Do not modify files.")
        await sm.execute()
        with get_db() as conn:
            report_row = conn.execute(
                "SELECT report_md FROM reports WHERE run_id = 'ro-report'"
            ).fetchone()
        assert report_row is not None
        assert "## Analysis" in report_row["report_md"]
        assert "Hello!" in report_row["report_md"]

    def test_failed_run_report_from_events_includes_analysis(self):
        now = now_iso()
        analysis = "Hello!\n\n- Backend orchestrator\n- React frontend\n- SQLite storage"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO runs (id, repo_path, task, status, provider, model,
                   runtime_mode, created_at, updated_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("fail-ro", "/tmp", "Summarize. Do not modify files.", "completed",
                 "nvidia", "openai/gpt-oss-120b", "local", now, now, now),
            )
            insert_event(
                conn, "fail-ro", "analysis_generated", phase="analysis",
                status="complete", message="Read-only analysis generated",
                payload={"analysis": analysis},
            )

        report = generate_report_from_run_record("fail-ro")
        assert "## Analysis" in report["report_md"]
        assert "Hello!" in report["report_md"]
        assert "Backend orchestrator" in report["report_md"]
        assert "mock" not in report["report_md"].lower()


class TestNonReadOnlyUnaffected:
    @pytest.mark.asyncio
    async def test_patching_still_runs_for_normal_task(self):
        insert_run("normal")
        sm = RunStateMachine("normal", TEST_REPO, "Fix the off-by-one error in pagination")
        await sm.execute()
        assert sm.phase_statuses[Phase.ANALYSIS] == PhaseStatus.SKIPPED
        assert sm.phase_statuses[Phase.PATCHING] in (PhaseStatus.COMPLETE, PhaseStatus.SKIPPED)
        with get_db() as conn:
            report_row = conn.execute(
                "SELECT report_md FROM reports WHERE run_id = 'normal'"
            ).fetchone()
        assert report_row is not None
        assert "## Analysis" not in report_row["report_md"]

    @pytest.mark.asyncio
    async def test_readme_mutation_skips_analysis_runs_patching(self):
        insert_run("mut-readme")
        task = (
            'Modify README.md by adding exactly this one sentence somewhere appropriate: '
            '"Docker NVIDIA mutation integration." Do not modify any other files.'
        )
        sm = RunStateMachine("mut-readme", TEST_REPO, task)
        assert sm.ctx.read_only is False
        await sm._phase_analysis()
        assert sm.phase_statuses[Phase.ANALYSIS] == PhaseStatus.SKIPPED
        await sm._phase_patching()
        assert sm.phase_statuses[Phase.PATCHING] != PhaseStatus.SKIPPED


class TestNvidiaReasoningExclusion:
    def test_reasoning_text_excluded_from_output(self):
        data = {
            "output": [
                {
                    "type": "reasoning",
                    "content": [{"type": "reasoning_text", "text": "Hidden chain of thought"}],
                },
                {
                    "type": "message",
                    "content": [
                        {"type": "reasoning_text", "text": "More hidden reasoning"},
                        {"type": "output_text", "text": "Hello!\n\n- One\n- Two\n- Three"},
                    ],
                },
            ]
        }
        result = _extract_output_text(data)
        assert "Hello!" in result
        assert "One" in result
        assert "Hidden chain of thought" not in result
        assert "More hidden reasoning" not in result
        assert "reasoning" not in result.lower()

    def test_top_level_output_text_preferred(self):
        data = {
            "output_text": "Visible answer",
            "reasoning_text": "Should never appear",
        }
        assert _extract_output_text(data) == "Visible answer"

    @pytest.mark.asyncio
    async def test_nvidia_analysis_role_uses_output_only(self):
        from patchquest.agents.roles import run_analysis_role

        async def mock_complete(self, messages, config, response_format=None):
            return ProviderResponse(
                content="Hello!\n\n- Bullet A\n- Bullet B\n- Bullet C",
                usage={"prompt_tokens": 10, "completion_tokens": 20},
                model="openai/gpt-oss-120b",
            )

        ctx = RunContext(
            run_id="nv-ro",
            repo_path="/tmp",
            task="Say hello and give 3 bullets. Do not modify files.",
            provider="nvidia",
            model="openai/gpt-oss-120b",
            read_only=True,
        )

        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-test"}):
            with patch.object(NvidiaProvider, "complete", mock_complete):
                analysis = await run_analysis_role(ctx)

        assert "Hello!" in analysis
        assert "Bullet A" in analysis
        assert "reasoning_text" not in analysis


# ======================================================================
# Final report
# ======================================================================

def test_report_includes_required_sections():
    ctx = RunContext(
        run_id="test-123",
        repo_path="/tmp/test-repo",
        task="Fix the login bug",
        applied_files=["src/auth.py"],
        commands_run=[{"command": "python -m pytest", "result": {"success": True}}],
        test_results=[{"command": "python -m pytest", "success": True}],
    )
    report = generate_report(ctx)

    md = report["report_md"]
    assert "test-123" in md
    assert "Fix the login bug" in md
    assert "src/auth.py" in md
    assert "pytest" in md
    assert "Security Scan" in md
    assert "Status" in md


def test_report_redacts_secrets():
    ctx = RunContext(
        run_id="test-456",
        repo_path="/tmp/test-repo",
        task="Update config",
        commands_run=[{
            "command": "cat config.py",
            "result": {"success": True, "stdout": 'key = "sk-notarealkeynotarealkeynotarealkey"'},
        }],
    )
    report = generate_report(ctx)
    md = report["report_md"]
    assert "sk-notarealkey" not in md


def test_report_with_no_changes():
    ctx = RunContext(
        run_id="test-789",
        repo_path="/tmp/test-repo",
        task="Investigate issue",
    )
    report = generate_report(ctx)
    md = report["report_md"]
    assert "No files were modified" in md
    assert "No commands were executed" in md


def test_report_includes_analysis_for_read_only():
    ctx = RunContext(
        run_id="test-ro",
        repo_path="/tmp/test-repo",
        task="Summarize repo. Do not modify files.",
        read_only=True,
        analysis="Hello!\n\n- Item 1\n- Item 2\n- Item 3",
    )
    report = generate_report(ctx)
    md = report["report_md"]
    assert "## Analysis" in md
    assert "Hello!" in md
    assert "Item 1" in md


def test_report_docker_runtime_from_context():
    ctx = RunContext(
        run_id="docker-ctx",
        repo_path="/tmp/test-repo",
        task="Inspect repo",
        provider="mock",
        runtime_mode="docker",
    )
    report = generate_report(ctx)
    assert "**Runtime:** docker" in report["report_md"]


def test_report_docker_runtime_from_run_record(tmp_path):
    from patchquest.database import get_db

    with get_db() as conn:
        conn.execute(
            """INSERT INTO runs (id, repo_path, task, status, provider, model, runtime_mode,
               memory_mode, allow_network, dry_run, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            ("docker-run", "/tmp/repo", "Inspect", "completed", "nvidia", "openai/gpt-oss-20b",
             "docker", "repo", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )

    report = generate_report_from_run_record("docker-run")
    assert "**Runtime:** docker" in report["report_md"]
    assert "**Provider:** nvidia" in report["report_md"]
    assert "**Model:** openai/gpt-oss-20b" in report["report_md"]

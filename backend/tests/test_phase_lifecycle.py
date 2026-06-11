"""Phase event lifecycle and end-to-end orchestration flow tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from patchquest.config import AppConfig, set_config
from patchquest.database import get_db, init_db, now_iso, set_db_path
from patchquest.orchestrator.phases import PHASE_ORDER, Phase, PhaseStatus
from patchquest.orchestrator.run_context import RunContext, _detect_read_only
from patchquest.orchestrator.state_machine import RunStateMachine
from patchquest.reports.final_report import generate_report

TERMINAL_PHASE_EVENTS = frozenset({
    "phase_completed", "phase_failed", "phase_skipped", "phase_blocked",
})


def _insert_run(run_id: str, task: str = "task") -> None:
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO runs (id, repo_path, task, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, "/tmp", task, "created", now, now),
        )


def _fetch_events(run_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, phase, status, message, payload_json FROM run_events "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    events = []
    for row in rows:
        events.append({
            "type": row["type"],
            "phase": row["phase"],
            "status": row["status"],
            "message": row["message"],
            "payload": json.loads(row["payload_json"]) if row["payload_json"] else None,
        })
    return events


def assert_every_phase_started_has_terminal_event(events: list[dict]) -> None:
    starts = [e for e in events if e["type"] == "phase_started"]
    for start in starts:
        phase = start["phase"]
        terminals = [
            e for e in events
            if e["phase"] == phase and e["type"] in TERMINAL_PHASE_EVENTS
        ]
        assert terminals, f"Phase {phase} has phase_started but no terminal event"
        assert len(terminals) == 1, f"Phase {phase} has {len(terminals)} terminal events, expected 1"


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    init_db()
    set_config(AppConfig())
    yield


@pytest.fixture
def mini_repo(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Mini Repo\n\nA tiny test repository.\n")
    return tmp_path


class TestReadOnlyClassifier:
    @pytest.mark.parametrize("task", [
        "Give a summary. Do not modify files.",
        "Analyze README.md and suggest changes, do not edit",
    ])
    def test_read_only_tasks(self, task: str):
        assert _detect_read_only(task, False) is True

    @pytest.mark.parametrize("task", [
        'Modify README.md by adding exactly this one sentence: "Hello". Do not modify any other files.',
        "Update README.md with one sentence. Keep the change minimal.",
        'Add exactly this sentence to README.md: "PatchQuest integration marker."',
        "Create a docs section, but don't change code files.",
    ])
    def test_mutating_tasks(self, task: str):
        assert _detect_read_only(task, False) is False


class TestPhaseLifecycle:
    @pytest.mark.asyncio
    async def test_mock_read_only_run_terminal_events(self, mini_repo):
        _insert_run("ro-life", "Summarize repo. Do not modify files.")
        sm = RunStateMachine(
            "ro-life", str(mini_repo),
            "Summarize repo. Do not modify files.",
            provider="mock",
        )
        await sm.execute()
        events = _fetch_events("ro-life")
        assert_every_phase_started_has_terminal_event(events)

    @pytest.mark.asyncio
    async def test_mock_mutating_run_terminal_events(self, mini_repo):
        task = (
            'Modify README.md by adding exactly this one sentence: '
            '"Docker runtime integration marker." Do not modify any other files.'
        )
        _insert_run("mut-life", task)
        sm = RunStateMachine("mut-life", str(mini_repo), task, provider="mock")
        assert sm.ctx.read_only is False
        await sm.execute()
        events = _fetch_events("mut-life")
        assert_every_phase_started_has_terminal_event(events)

        skipped = {e["phase"] for e in events if e["type"] == "phase_skipped"}
        assert "analysis" in skipped
        assert "patching" not in skipped

    @pytest.mark.asyncio
    async def test_static_checks_emits_skipped_when_none(self, mini_repo):
        _insert_run("skip-static", "Summarize. Do not modify files.")
        sm = RunStateMachine(
            "skip-static", str(mini_repo),
            "Summarize. Do not modify files.",
            provider="mock",
        )
        await sm.execute()
        events = _fetch_events("skip-static")
        static = [e for e in events if e["phase"] == "static_checks"]
        assert any(e["type"] == "phase_skipped" for e in static)
        assert any("No static checks detected" in (e.get("message") or "") for e in static)

    @pytest.mark.asyncio
    async def test_testing_emits_skipped_when_none(self, mini_repo):
        _insert_run("skip-test", "Summarize. Do not modify files.")
        sm = RunStateMachine(
            "skip-test", str(mini_repo),
            "Summarize. Do not modify files.",
            provider="mock",
        )
        sm.ctx.test_commands = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "patchquest.tools.test_runner.detect_test_commands",
                lambda _repo: [],
            )
            await sm._phase_testing()
        events = _fetch_events("skip-test")
        testing = [e for e in events if e["phase"] == "testing"]
        assert any(e["type"] == "phase_skipped" for e in testing)
        assert sm.phase_statuses[Phase.TESTING] == PhaseStatus.SKIPPED


class TestMockE2EFlows:
    @pytest.mark.asyncio
    async def test_mock_read_only_includes_analysis_no_file_changes(self, mini_repo):
        _insert_run("mock-ro", "Summarize repo. Do not modify files.")
        sm = RunStateMachine(
            "mock-ro", str(mini_repo),
            "Summarize repo. Do not modify files.",
            provider="mock",
        )
        await sm.execute()

        with get_db() as conn:
            run = conn.execute("SELECT status FROM runs WHERE id='mock-ro'").fetchone()
            report = conn.execute(
                "SELECT report_md FROM reports WHERE run_id='mock-ro'"
            ).fetchone()

        assert run["status"] == "completed"
        assert report is not None
        assert "## Analysis" in report["report_md"]
        assert "Read-only" in report["report_md"]
        assert (mini_repo / "README.md").read_text() == "# Mini Repo\n\nA tiny test repository.\n"

        events = _fetch_events("mock-ro")
        assert any(e["type"] == "analysis_generated" for e in events)
        assert any(e["type"] == "phase_skipped" and e["phase"] == "patching" for e in events)

    @pytest.mark.asyncio
    async def test_mock_mutating_applies_readme_patch(self, mini_repo):
        sentence = "Docker NVIDIA mutation integration: PatchQuest can apply a minimal README change."
        task = (
            f'Modify README.md by adding exactly this one sentence somewhere appropriate: '
            f'"{sentence}" Keep the change minimal. Do not modify any other files.'
        )
        _insert_run("mock-mut", task)
        sm = RunStateMachine(
            "mock-mut", str(mini_repo), task,
            provider="mock", runtime_mode="docker",
        )
        assert sm.ctx.read_only is False
        await sm.execute()

        readme_text = (mini_repo / "README.md").read_text()
        assert sentence in readme_text

        with get_db() as conn:
            report = conn.execute(
                "SELECT report_md, diff_patch FROM reports WHERE run_id='mock-mut'"
            ).fetchone()

        assert report is not None
        assert "Read-only" not in report["report_md"]
        assert "## Analysis" not in report["report_md"]
        assert "README.md" in report["report_md"]
        assert report["diff_patch"]

        events = _fetch_events("mock-mut")
        assert any(e["type"] == "patch_proposed" for e in events)
        assert any(e["type"] == "patch_applied" for e in events)
        assert any(e["type"] == "phase_skipped" and e["phase"] == "analysis" for e in events)

    @pytest.mark.asyncio
    async def test_docker_runtime_mode_preserved_in_report(self, mini_repo):
        task = (
            'Modify README.md by adding exactly this one sentence: '
            '"Runtime docker preserved." Do not modify any other files.'
        )
        _insert_run("mock-docker", task)
        sm = RunStateMachine(
            "mock-docker", str(mini_repo), task,
            provider="mock", runtime_mode="docker",
        )
        await sm.execute()

        report = generate_report(sm.ctx)
        assert "**Runtime:** docker" in report["report_md"]
        assert sm.ctx.read_only is False


class TestNvidiaReasoningNotInReport:
    def test_reasoning_excluded_from_extracted_output(self):
        from patchquest.agents.providers_nvidia import _extract_output_text

        data = {
            "reasoning_text": "Hidden internal reasoning chain",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "reasoning_text", "text": "Step by step thinking"},
                        {"type": "output_text", "text": "Hello!\n\n- One\n- Two\n- Three"},
                    ],
                }
            ],
        }
        text = _extract_output_text(data)
        assert "Hello!" in text
        assert "Hidden" not in text
        assert "Step by step" not in text

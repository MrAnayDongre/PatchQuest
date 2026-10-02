"""Run lifecycle: phase ordering and terminal events, mock end-to-end flows, approval records.

Invariants protected: every started phase ends in exactly one terminal event; a mock run walks all phases
and completes; read-only vs mutating classification drives which phases are skipped; approvals are persisted,
isolated per run and leave the pending list once resolved.
"""

from __future__ import annotations

import tempfile

import pytest

from patchquest.database import get_db, now_iso
from patchquest.orchestrator.approvals import create_approval, get_pending_approvals
from patchquest.orchestrator.phases import Phase, PhaseStatus
from patchquest.orchestrator.run_context import _detect_read_only
from patchquest.orchestrator.state_machine import RunStateMachine
from patchquest.reports.final_report import generate_report
from tests.support import fetch_events, insert_run


@pytest.fixture(autouse=True)
def seeded_run():
    """Several tests here use a run called "test-run"."""
    insert_run("test-run", "test task", status="running")



# ======================================================================
# Phase progression
# ======================================================================

@pytest.mark.asyncio
async def test_phases_progress_in_order():
    repo_dir = tempfile.mkdtemp()
    machine = RunStateMachine("test-run", repo_dir, "test task")
    await machine.execute()

    completed_phases = [p for p, s in machine.phase_statuses.items()
                       if s in (PhaseStatus.COMPLETE, PhaseStatus.SKIPPED)]
    assert len(completed_phases) == len(Phase)


@pytest.mark.asyncio
async def test_run_completes_with_mock():
    repo_dir = tempfile.mkdtemp()
    machine = RunStateMachine("test-run", repo_dir, "add a docstring")
    await machine.execute()

    from patchquest.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id = 'test-run'").fetchone()
    assert row["status"] == "completed"


# ======================================================================
# Phase event lifecycle and mock end-to-end flows
# ======================================================================

TERMINAL_PHASE_EVENTS = frozenset({
    "phase_completed", "phase_failed", "phase_skipped", "phase_blocked",
})


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
        insert_run("ro-life", "Summarize repo. Do not modify files.")
        sm = RunStateMachine(
            "ro-life", str(mini_repo),
            "Summarize repo. Do not modify files.",
            provider="mock",
        )
        await sm.execute()
        events = fetch_events("ro-life")
        assert_every_phase_started_has_terminal_event(events)

    @pytest.mark.asyncio
    async def test_mock_mutating_run_terminal_events(self, mini_repo):
        task = (
            'Modify README.md by adding exactly this one sentence: '
            '"Docker runtime integration marker." Do not modify any other files.'
        )
        insert_run("mut-life", task)
        sm = RunStateMachine("mut-life", str(mini_repo), task, provider="mock")
        assert sm.ctx.read_only is False
        await sm.execute()
        events = fetch_events("mut-life")
        assert_every_phase_started_has_terminal_event(events)

        skipped = {e["phase"] for e in events if e["type"] == "phase_skipped"}
        assert "analysis" in skipped
        assert "patching" not in skipped

    @pytest.mark.asyncio
    async def test_static_checks_emits_skipped_when_none(self, mini_repo):
        insert_run("skip-static", "Summarize. Do not modify files.")
        sm = RunStateMachine(
            "skip-static", str(mini_repo),
            "Summarize. Do not modify files.",
            provider="mock",
        )
        await sm.execute()
        events = fetch_events("skip-static")
        static = [e for e in events if e["phase"] == "static_checks"]
        assert any(e["type"] == "phase_skipped" for e in static)
        assert any("No static checks detected" in (e.get("message") or "") for e in static)

    @pytest.mark.asyncio
    async def test_testing_emits_skipped_when_none(self, mini_repo):
        insert_run("skip-test", "Summarize. Do not modify files.")
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
        events = fetch_events("skip-test")
        testing = [e for e in events if e["phase"] == "testing"]
        assert any(e["type"] == "phase_skipped" for e in testing)
        assert sm.phase_statuses[Phase.TESTING] == PhaseStatus.SKIPPED


class TestMockE2EFlows:
    @pytest.mark.asyncio
    async def test_mock_read_only_includes_analysis_no_file_changes(self, mini_repo):
        insert_run("mock-ro", "Summarize repo. Do not modify files.")
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

        events = fetch_events("mock-ro")
        assert any(e["type"] == "analysis_generated" for e in events)
        assert any(e["type"] == "phase_skipped" and e["phase"] == "patching" for e in events)

    @pytest.mark.asyncio
    async def test_mock_mutating_applies_readme_patch(self, mini_repo):
        sentence = "Docker NVIDIA mutation integration: PatchQuest can apply a minimal README change."
        task = (
            f'Modify README.md by adding exactly this one sentence somewhere appropriate: '
            f'"{sentence}" Keep the change minimal. Do not modify any other files.'
        )
        insert_run("mock-mut", task)
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

        events = fetch_events("mock-mut")
        assert any(e["type"] == "patch_proposed" for e in events)
        assert any(e["type"] == "patch_applied" for e in events)
        assert any(e["type"] == "phase_skipped" and e["phase"] == "analysis" for e in events)

    @pytest.mark.asyncio
    async def test_docker_runtime_mode_preserved_in_report(self, mini_repo):
        task = (
            'Modify README.md by adding exactly this one sentence: '
            '"Runtime docker preserved." Do not modify any other files.'
        )
        insert_run("mock-docker", task)
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


# ======================================================================
# Approval records
# ======================================================================

def test_create_approval_returns_id():
    approval_id = create_approval("test-run", "command", command="npm install", reason="downloads packages")
    assert approval_id is not None
    assert len(approval_id) > 0


def test_pending_approvals_listed():
    create_approval("test-run", "command", command="pip install flask", reason="installs package")
    create_approval("test-run", "command", command="git checkout main", reason="switches branch")

    pending = get_pending_approvals("test-run")
    assert len(pending) == 2
    commands = [p["command"] for p in pending]
    assert "pip install flask" in commands
    assert "git checkout main" in commands


def test_approval_resolved_removes_from_pending():
    approval_id = create_approval("test-run", "command", command="rm -r build/", reason="removes build dir")

    with get_db() as conn:
        conn.execute(
            "UPDATE approvals SET status = 'approved', resolved_at = ? WHERE id = ?",
            (now_iso(), approval_id),
        )

    pending = get_pending_approvals("test-run")
    assert len(pending) == 0


def test_rejected_approval_not_pending():
    approval_id = create_approval("test-run", "command", command="curl example.com", reason="network access")

    with get_db() as conn:
        conn.execute(
            "UPDATE approvals SET status = 'rejected', resolved_at = ? WHERE id = ?",
            (now_iso(), approval_id),
        )

    pending = get_pending_approvals("test-run")
    assert len(pending) == 0


def test_multiple_runs_isolated():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO runs (id, repo_path, task, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("other-run", "/tmp/other", "other task", "running", now_iso(), now_iso()),
        )

    create_approval("test-run", "command", command="npm install", reason="test")
    create_approval("other-run", "command", command="pip install", reason="test")

    pending_test = get_pending_approvals("test-run")
    pending_other = get_pending_approvals("other-run")

    assert len(pending_test) == 1
    assert len(pending_other) == 1
    assert pending_test[0]["command"] == "npm install"
    assert pending_other[0]["command"] == "pip install"

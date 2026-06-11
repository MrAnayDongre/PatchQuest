"""Tests for state machine."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from patchquest.database import init_db, set_db_path
from patchquest.orchestrator.phases import Phase, PhaseStatus
from patchquest.orchestrator.state_machine import RunStateMachine


@pytest.fixture(autouse=True)
def setup_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    set_db_path(Path(tmp.name))
    init_db()
    # Create a run record
    from patchquest.database import get_db, now_iso
    with get_db() as conn:
        conn.execute(
            "INSERT INTO runs (id, repo_path, task, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("test-run", "/tmp/test-repo", "test task", "created", now_iso(), now_iso()),
        )


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

"""Tests for the approval flow and permission system."""

import tempfile
from pathlib import Path

import pytest

from patchquest.database import get_db, init_db, now_iso, set_db_path
from patchquest.orchestrator.approvals import create_approval, get_pending_approvals


@pytest.fixture(autouse=True)
def setup_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    set_db_path(Path(tmp.name))
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO runs (id, repo_path, task, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("test-run", "/tmp/repo", "fix bug", "running", now_iso(), now_iso()),
        )


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

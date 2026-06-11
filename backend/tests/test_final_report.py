"""Tests for final report generation."""

from patchquest.orchestrator.run_context import RunContext
from patchquest.reports.final_report import generate_report, generate_report_from_run_record


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
            "result": {"success": True, "stdout": 'key = "sk-abc123def456ghi789jkl012mno345pqr678"'},
        }],
    )
    report = generate_report(ctx)
    md = report["report_md"]
    assert "sk-abc123def456" not in md


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
    import sqlite3
    from patchquest.database import init_db, set_db_path

    db_path = tmp_path / "report.db"
    set_db_path(db_path)
    init_db()

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO runs (id, repo_path, task, status, provider, model, runtime_mode,
               memory_mode, allow_network, dry_run, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            ("docker-run", "/tmp/repo", "Inspect", "completed", "nvidia", "openai/gpt-oss-20b",
             "docker", "repo", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

    report = generate_report_from_run_record("docker-run")
    assert "**Runtime:** docker" in report["report_md"]
    assert "**Provider:** nvidia" in report["report_md"]
    assert "**Model:** openai/gpt-oss-20b" in report["report_md"]

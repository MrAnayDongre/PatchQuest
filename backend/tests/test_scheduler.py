"""Tests for the scheduler service and schedule calculator."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from patchquest.database import get_db, init_db, now_iso, set_db_path
from patchquest.scheduler.schedule_calculator import compute_next_run
from patchquest.scheduler.scheduler import (
    create_task,
    delete_task,
    get_due_tasks,
    get_history,
    get_task,
    list_tasks,
    pause_task,
    resume_task,
    run_task_now,
    update_task,
    validate_timezone,
    _provider_config_error,
)


@pytest.fixture(autouse=True)
def setup_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    set_db_path(Path(tmp.name))
    init_db()


class TestScheduleCalculator:
    def test_one_shot_uses_expr(self):
        result = compute_next_run("one_shot", "2026-12-25T10:00:00+00:00", "UTC")
        assert "2026-12-25" in result

    def test_interval_adds_minutes(self):
        now = datetime.now(timezone.utc)
        result = compute_next_run("interval", "30", "UTC", from_time=now)
        next_dt = datetime.fromisoformat(result)
        assert next_dt > now
        diff = (next_dt - now).total_seconds()
        assert 1700 < diff < 1900  # ~30 minutes

    def test_interval_hours(self):
        now = datetime.now(timezone.utc)
        result = compute_next_run("interval", "2h", "UTC", from_time=now)
        next_dt = datetime.fromisoformat(result)
        diff = (next_dt - now).total_seconds()
        assert 7100 < diff < 7300  # ~2 hours

    def test_daily_next_run(self):
        now = datetime(2026, 6, 7, 15, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run("daily", "09:00", "UTC", from_time=now)
        next_dt = datetime.fromisoformat(result)
        assert next_dt.hour == 9
        assert next_dt.day == 8  # next day since 09:00 already passed

    def test_daily_same_day_if_future(self):
        now = datetime(2026, 6, 7, 6, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run("daily", "09:00", "UTC", from_time=now)
        next_dt = datetime.fromisoformat(result)
        assert next_dt.hour == 9
        assert next_dt.day == 7  # same day

    def test_weekly_next_run(self):
        # 2026-06-07 is a Sunday (weekday=6)
        now = datetime(2026, 6, 7, 15, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run("weekly", "mon,09:00", "UTC", from_time=now)
        next_dt = datetime.fromisoformat(result)
        assert next_dt.weekday() == 0  # Monday
        assert next_dt.hour == 9

    def test_cron_fallback(self):
        now = datetime(2026, 6, 7, 15, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run("cron", "30 8 * * *", "UTC", from_time=now)
        next_dt = datetime.fromisoformat(result)
        assert next_dt > now


class TestSchedulerCRUD:
    def test_create_task(self):
        task_id = create_task(
            title="Test Task",
            task_prompt="Fix the bug",
            repo_path="/tmp/repo",
            schedule_type="one_shot",
            schedule_expr="2026-12-25T10:00:00+00:00",
        )
        assert task_id > 0
        task = get_task(task_id)
        assert task is not None
        assert task["title"] == "Test Task"
        assert task["status"] == "active"

    def test_list_tasks(self):
        create_task(title="A", task_prompt="t", repo_path="/tmp", schedule_type="one_shot")
        create_task(title="B", task_prompt="t", repo_path="/tmp", schedule_type="daily", schedule_expr="09:00")
        tasks = list_tasks()
        assert len(tasks) == 2

    def test_update_task(self):
        tid = create_task(title="Old", task_prompt="t", repo_path="/tmp")
        update_task(tid, title="New")
        task = get_task(tid)
        assert task["title"] == "New"

    def test_delete_task(self):
        tid = create_task(title="Del", task_prompt="t", repo_path="/tmp")
        assert delete_task(tid)
        tasks = list_tasks()
        assert len(tasks) == 0

    def test_invalid_timezone_rejected(self):
        with pytest.raises(ValueError, match="Invalid timezone"):
            create_task(title="T", task_prompt="t", repo_path="/tmp", tz="Fake/Zone")

    def test_empty_timezone_rejected(self):
        with pytest.raises(ValueError, match="Timezone is required"):
            create_task(title="T", task_prompt="t", repo_path="/tmp", tz="")
        with pytest.raises(ValueError, match="Timezone is required"):
            validate_timezone("   ")

    def test_pause_prevents_execution(self):
        tid = create_task(
            title="Pausable", task_prompt="t", repo_path="/tmp",
            schedule_type="one_shot",
            next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
        pause_task(tid)
        due = get_due_tasks()
        assert len(due) == 0

    def test_resume_allows_execution(self):
        tid = create_task(title="Resumable", task_prompt="t", repo_path="/tmp")
        pause_task(tid)
        resume_task(tid)
        task = get_task(tid)
        assert task["status"] == "active"
        assert task["enabled"] == 1

    def test_disabled_task_does_not_run(self):
        create_task(
            title="Disabled", task_prompt="t", repo_path="/tmp",
            schedule_type="one_shot",
            next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
        # Disable via pause
        tasks = list_tasks()
        pause_task(tasks[0]["id"])
        due = get_due_tasks()
        assert len(due) == 0


class TestSchedulerExecution:
    def test_run_now_creates_history(self):
        tid = create_task(title="RunNow", task_prompt="test task", repo_path="/tmp")
        run_id = run_task_now(tid)
        assert run_id is not None
        history = get_history(tid)
        assert len(history) >= 1
        assert history[0]["run_id"] == run_id
        assert history[0]["status"] in ("started", "completed")

    def test_due_task_detected(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        create_task(
            title="Due", task_prompt="t", repo_path="/tmp",
            schedule_type="one_shot", next_run_at=past,
        )
        due = get_due_tasks()
        assert len(due) == 1

    def test_future_task_not_due(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        create_task(
            title="Future", task_prompt="t", repo_path="/tmp",
            schedule_type="one_shot", next_run_at=future,
        )
        due = get_due_tasks()
        assert len(due) == 0

    def test_scheduled_docker_runtime_stored(self):
        tid = create_task(
            title="Docker Task", task_prompt="t", repo_path="/tmp",
            runtime_mode="docker",
        )
        task = get_task(tid)
        assert task["runtime_mode"] == "docker"

    def test_run_now_creates_real_run_record(self):
        tid = create_task(title="Real Run", task_prompt="add docstring", repo_path="/tmp")
        run_id = run_task_now(tid)
        assert run_id is not None
        with get_db() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert run is not None
        assert run["task"] == "add docstring"
        assert run["repo_path"] == "/tmp"
        assert run["provider"] == "mock"

    def test_create_task_stores_provider_model_runtime_memory(self):
        tid = create_task(
            title="NVIDIA Task",
            task_prompt="analyze",
            repo_path="/tmp",
            provider="nvidia",
            model="openai/gpt-oss-20b",
            runtime_mode="docker",
            memory_mode="repo",
        )
        task = get_task(tid)
        assert task["provider"] == "nvidia"
        assert task["model"] == "openai/gpt-oss-20b"
        assert task["runtime_mode"] == "docker"
        assert task["memory_mode"] == "repo"

    def test_update_task_provider_model(self):
        tid = create_task(title="Up", task_prompt="t", repo_path="/tmp")
        update_task(tid, provider="groq", model="llama-3.1-8b-instant", runtime_mode="docker")
        task = get_task(tid)
        assert task["provider"] == "groq"
        assert task["model"] == "llama-3.1-8b-instant"
        assert task["runtime_mode"] == "docker"

    def test_nvidia_task_creates_run_with_provider_model(self, monkeypatch):
        monkeypatch.setattr(
            "patchquest.scheduler.scheduler._provider_config_error",
            lambda provider: None,
        )
        tid = create_task(
            title="NVIDIA Run",
            task_prompt="summarize",
            repo_path="/tmp",
            provider="nvidia",
            model="openai/gpt-oss-20b",
            runtime_mode="docker",
        )
        run_id = run_task_now(tid)
        assert run_id is not None
        with get_db() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert run["provider"] == "nvidia"
        assert run["model"] == "openai/gpt-oss-20b"
        assert run["runtime_mode"] == "docker"

    def test_mock_task_creates_mock_run_intentionally(self):
        tid = create_task(
            title="Mock Run",
            task_prompt="demo",
            repo_path="/tmp",
            provider="mock",
        )
        run_id = run_task_now(tid)
        with get_db() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert run["provider"] == "mock"

    def test_unconfigured_provider_fails_with_report(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        tid = create_task(
            title="Missing Key",
            task_prompt="fail",
            repo_path="/tmp",
            provider="nvidia",
            model="openai/gpt-oss-20b",
        )
        run_id = run_task_now(tid)
        assert run_id is not None
        history = get_history(tid)
        assert history[0]["status"] == "failed"
        assert "NVIDIA_API_KEY" in (history[0].get("message") or "")
        with get_db() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            report = conn.execute("SELECT * FROM reports WHERE run_id = ?", (run_id,)).fetchone()
        assert run["status"] == "failed"
        assert run["provider"] == "nvidia"
        assert report is not None

    def test_history_includes_provider_model_runtime(self, monkeypatch):
        monkeypatch.setattr(
            "patchquest.scheduler.scheduler._provider_config_error",
            lambda provider: None,
        )
        tid = create_task(
            title="History Meta",
            task_prompt="t",
            repo_path="/tmp",
            provider="nvidia",
            model="openai/gpt-oss-20b",
            runtime_mode="docker",
        )
        run_id = run_task_now(tid)
        history = get_history(tid)
        entry = next(h for h in history if h["run_id"] == run_id)
        assert entry["provider"] == "nvidia"
        assert entry["model"] == "openai/gpt-oss-20b"
        assert entry["runtime_mode"] == "docker"

    def test_one_shot_clears_next_run_at_after_execution(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        tid = create_task(
            title="One Shot",
            task_prompt="t",
            repo_path="/tmp",
            schedule_type="one_shot",
            next_run_at=past,
        )
        run_task_now(tid)
        task = get_task(tid)
        assert task["status"] == "completed"
        assert task["next_run_at"] is None

    def test_active_task_before_due_stays_active(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        tid = create_task(
            title="Future",
            task_prompt="t",
            repo_path="/tmp",
            schedule_type="one_shot",
            next_run_at=future,
        )
        task = get_task(tid)
        assert task["status"] == "active"
        assert task["next_run_at"] is not None

    def test_provider_config_error_for_missing_key(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        err = _provider_config_error("nvidia")
        assert err is not None
        assert "NVIDIA_API_KEY" in err

    def test_provider_config_error_mock_always_ok(self):
        assert _provider_config_error("mock") is None

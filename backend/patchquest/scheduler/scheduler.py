"""Scheduler service — creates, manages, and executes scheduled PatchQuest runs."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from patchquest.database import get_db, insert_event, now_iso
from patchquest.scheduler.schedule_calculator import compute_next_run

logger = logging.getLogger(__name__)


def create_task(
    title: str,
    task_prompt: str,
    repo_path: str,
    schedule_type: str = "one_shot",
    schedule_expr: str | None = None,
    tz: str = "UTC",
    provider: str = "mock",
    model: str | None = None,
    runtime_mode: str = "local",
    model_profile: str | None = None,
    memory_mode: str = "repo",
    next_run_at: str | None = None,
) -> int:
    validate_timezone(tz)
    now = now_iso()

    if not next_run_at:
        next_run_at = compute_next_run(schedule_type, schedule_expr, tz)

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO scheduled_tasks
               (title, task_prompt, repo_path, schedule_type, schedule_expr, timezone,
                next_run_at, enabled, status, provider, model, runtime_mode, model_profile,
                memory_mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?, ?, ?, ?, ?)""",
            (title, task_prompt, repo_path, schedule_type, schedule_expr, tz,
             next_run_at, provider, model, runtime_mode, model_profile, memory_mode, now, now),
        )
        return cursor.lastrowid  # type: ignore


def list_tasks(include_deleted: bool = False) -> list[dict]:
    query = "SELECT * FROM scheduled_tasks"
    if not include_deleted:
        query += " WHERE status != 'deleted'"
    query += " ORDER BY next_run_at"
    with get_db() as conn:
        rows = conn.execute(query).fetchall()
    return [_normalize_task_row(dict(r)) for r in rows]


def get_task(task_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    return _normalize_task_row(dict(row)) if row else None


def _normalize_task_row(task: dict) -> dict:
    """Ensure provider defaults explicitly for rows created before migration."""
    if not task.get("provider"):
        task["provider"] = "mock"
    return task


def update_task(task_id: int, **fields: Any) -> bool:
    allowed = {"title", "task_prompt", "repo_path", "schedule_type", "schedule_expr",
               "timezone", "provider", "model", "runtime_mode", "model_profile",
               "memory_mode", "next_run_at", "enabled"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False

    if "timezone" in updates:
        validate_timezone(updates["timezone"])

    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE scheduled_tasks SET {set_clause} WHERE id = ?", values)
        return cursor.rowcount > 0


def pause_task(task_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE scheduled_tasks SET status = 'paused', enabled = 0, updated_at = ? WHERE id = ? AND status IN ('active', 'running')",
            (now_iso(), task_id),
        )
        return cursor.rowcount > 0


def resume_task(task_id: int) -> bool:
    now = now_iso()
    with get_db() as conn:
        task = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            return False

        next_run = compute_next_run(task["schedule_type"], task["schedule_expr"], task["timezone"])
        conn.execute(
            "UPDATE scheduled_tasks SET status = 'active', enabled = 1, next_run_at = ?, updated_at = ? WHERE id = ?",
            (next_run, now, task_id),
        )
        return True


def delete_task(task_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE scheduled_tasks SET status = 'deleted', enabled = 0, updated_at = ? WHERE id = ?",
            (now_iso(), task_id),
        )
        return cursor.rowcount > 0


def run_task_now(task_id: int) -> str | None:
    """Immediately execute a scheduled task. Returns the run_id."""
    task = get_task(task_id)
    if not task:
        return None
    return _execute_task(task)


def get_due_tasks(now_dt: datetime | None = None) -> list[dict]:
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    now_str = now_dt.isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM scheduled_tasks
               WHERE enabled = 1 AND status = 'active' AND next_run_at <= ?
               ORDER BY next_run_at""",
            (now_str,),
        ).fetchall()
    return [_normalize_task_row(dict(r)) for r in rows]


def run_due_tasks() -> list[str]:
    """Find and execute all due tasks. Returns list of run_ids created."""
    due = get_due_tasks()
    run_ids = []
    for task in due:
        if _is_task_already_running(task["id"]):
            continue
        run_id = _execute_task(task)
        if run_id:
            run_ids.append(run_id)
    return run_ids


def get_history(task_id: int, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT h.*, r.provider, r.model, r.runtime_mode
               FROM scheduled_run_history h
               LEFT JOIN runs r ON r.id = h.run_id
               WHERE h.scheduled_task_id = ?
               ORDER BY h.started_at DESC LIMIT ?""",
            (task_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def record_history(task_id: int, run_id: str | None, status: str = "started", message: str | None = None) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO scheduled_run_history (scheduled_task_id, run_id, started_at, status, message) VALUES (?, ?, ?, ?, ?)",
            (task_id, run_id, now_iso(), status, message),
        )
        return cursor.lastrowid  # type: ignore


def update_history_status(run_id: str, status: str, message: str | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE scheduled_run_history SET status = ?, finished_at = ?, message = ? WHERE run_id = ?",
            (status, now_iso(), message, run_id),
        )


def validate_timezone(tz: str) -> None:
    if not tz or not str(tz).strip():
        raise ValueError("Timezone is required. Use an IANA timezone like America/Los_Angeles.")
    try:
        ZoneInfo(str(tz).strip())
    except (ZoneInfoNotFoundError, KeyError):
        raise ValueError(f"Invalid timezone: {tz}")


def _provider_config_error(provider: str) -> str | None:
    """Return a user-readable error if the provider is not configured, else None."""
    if provider in ("mock", "ollama", "openai_compatible"):
        return None

    from patchquest.api.routes_providers import PROVIDER_CATALOG

    catalog_entry = next((p for p in PROVIDER_CATALOG if p["name"] == provider), None)
    if not catalog_entry:
        return f"Unknown LLM provider '{provider}'."

    key_env = catalog_entry.get("api_key_env")
    if key_env and not os.environ.get(key_env, ""):
        return f"Provider '{provider}' is not configured. Set environment variable {key_env}."
    return None


def _execute_task(task: dict) -> str | None:
    """Create a real PatchQuest run from a scheduled task."""
    import uuid

    task = _normalize_task_row(task)
    provider = task.get("provider") or "mock"
    model = task.get("model")
    runtime_mode = task.get("runtime_mode") or "local"
    memory_mode = task.get("memory_mode") or "repo"

    provider_err = _provider_config_error(provider)
    if provider_err:
        return _execute_provider_failure(task, provider_err, provider, model, runtime_mode, memory_mode)

    run_id = str(uuid.uuid4())
    now = now_iso()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO runs (id, repo_path, task, status, provider, model, runtime_mode,
               model_profile, memory_mode, allow_network, dry_run, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            (run_id, task["repo_path"], task["task_prompt"], "created",
             provider, model, runtime_mode, task.get("model_profile"), memory_mode, now, now),
        )
        conn.execute(
            "UPDATE scheduled_tasks SET last_run_at = ?, status = 'running', updated_at = ? WHERE id = ?",
            (now, now, task["id"]),
        )

    record_history(task["id"], run_id, "started")
    _maybe_create_calendar_event(task, run_id, now)
    _advance_task_schedule(task, now)

    import asyncio
    from patchquest.orchestrator.state_machine import RunStateMachine

    machine = RunStateMachine(
        run_id, task["repo_path"], task["task_prompt"],
        provider=provider, model=model, runtime_mode=runtime_mode,
    )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run_and_record(machine, task["id"], run_id))
    except RuntimeError:
        asyncio.run(_run_and_record(machine, task["id"], run_id))

    return run_id


def _execute_provider_failure(
    task: dict,
    error_message: str,
    provider: str,
    model: str | None,
    runtime_mode: str,
    memory_mode: str,
) -> str:
    """Create a failed run when the selected provider is unavailable."""
    import uuid

    run_id = str(uuid.uuid4())
    now = now_iso()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO runs (id, repo_path, task, status, provider, model, runtime_mode,
               model_profile, memory_mode, allow_network, dry_run, created_at, updated_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
            (run_id, task["repo_path"], task["task_prompt"], "failed",
             provider, model, runtime_mode, task.get("model_profile"), memory_mode, now, now, now),
        )
        insert_event(conn, run_id, "run_failed", phase="init", status="failed", message=error_message)
        conn.execute(
            "UPDATE scheduled_tasks SET last_run_at = ?, updated_at = ? WHERE id = ?",
            (now, now, task["id"]),
        )

    from patchquest.reports.final_report import generate_report_from_run_record

    report = generate_report_from_run_record(run_id)
    with get_db() as conn:
        conn.execute(
            """INSERT INTO reports (run_id, report_md, diff_patch, commands_log, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, report["report_md"], report.get("diff_patch", ""), report.get("commands_log", ""), now),
        )

    record_history(task["id"], run_id, "failed", error_message)
    _advance_task_schedule(task, now)
    return run_id


def _advance_task_schedule(task: dict, now: str) -> None:
    if task["schedule_type"] != "one_shot":
        next_run = compute_next_run(task["schedule_type"], task["schedule_expr"], task["timezone"])
        with get_db() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET next_run_at = ?, status = 'active', updated_at = ? WHERE id = ?",
                (next_run, now, task["id"]),
            )
    else:
        with get_db() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET status = 'completed', enabled = 0, next_run_at = NULL, updated_at = ? WHERE id = ?",
                (now, task["id"]),
            )


async def _run_and_record(machine, task_id: int, run_id: str) -> None:
    try:
        await machine.execute()
        update_history_status(run_id, "completed")
    except Exception as e:
        update_history_status(run_id, "failed", str(e))


def _is_task_already_running(task_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM scheduled_run_history WHERE scheduled_task_id = ? AND status = 'started'",
            (task_id,),
        ).fetchone()
    return row["c"] > 0 if row else False


def _maybe_create_calendar_event(task: dict, run_id: str, now: str) -> None:
    try:
        from patchquest.config import get_config
        cfg = get_config()
        if not cfg.calendar.enabled or not cfg.calendar.create_events_for_scheduled_tasks:
            return

        if cfg.calendar.avoid_busy_times and task.get("next_run_at"):
            from patchquest.calendar.calendar_service import check_conflicts
            conflicts = check_conflicts(task["next_run_at"], now)
            if conflicts:
                logger.warning("Calendar conflict detected for task %s, creating event anyway", task["id"])

        from patchquest.calendar.calendar_service import create_scheduled_task_event
        create_scheduled_task_event(
            task_id=task["id"],
            title=task["title"],
            start_at=now,
            duration_minutes=30,
            reminder_minutes=cfg.calendar.reminder_minutes_before,
            description=f"PatchQuest run {run_id} for: {task['task_prompt'][:200]}",
            patchquest_run_id=run_id,
        )
    except Exception as e:
        logger.debug("Calendar event creation skipped: %s", e)

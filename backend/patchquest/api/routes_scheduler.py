"""Scheduler API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from patchquest.scheduler.scheduler import (
    create_task,
    delete_task,
    get_history,
    get_task,
    list_tasks,
    pause_task,
    resume_task,
    run_task_now,
    update_task,
)
from patchquest.scheduler.scheduler_loop import is_running
from patchquest.scheduler.scheduler_models import (
    CreateScheduledTaskRequest,
    ScheduledRunHistoryResponse,
    ScheduledTaskResponse,
    SchedulerStatusResponse,
    UpdateScheduledTaskRequest,
)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/status", response_model=SchedulerStatusResponse)
async def scheduler_status() -> SchedulerStatusResponse:
    from patchquest.database import get_db
    with get_db() as conn:
        active = conn.execute("SELECT COUNT(*) as c FROM scheduled_tasks WHERE enabled = 1 AND status = 'active'").fetchone()
        next_due = conn.execute("SELECT next_run_at FROM scheduled_tasks WHERE enabled = 1 AND status = 'active' ORDER BY next_run_at LIMIT 1").fetchone()

    return SchedulerStatusResponse(
        enabled=True,
        running=is_running(),
        poll_interval_seconds=30,
        active_tasks=active["c"] if active else 0,
        next_due=next_due["next_run_at"] if next_due else None,
    )


@router.get("/tasks", response_model=list[ScheduledTaskResponse])
async def get_tasks() -> list[ScheduledTaskResponse]:
    tasks = list_tasks()
    return [_to_response(t) for t in tasks]


@router.post("/tasks", response_model=ScheduledTaskResponse)
async def create_scheduled_task(req: CreateScheduledTaskRequest) -> ScheduledTaskResponse:
    try:
        task_id = create_task(
            title=req.title,
            task_prompt=req.task_prompt,
            repo_path=req.repo_path,
            schedule_type=req.schedule_type,
            schedule_expr=req.schedule_expr,
            tz=req.timezone,
            provider=req.provider,
            model=req.model,
            runtime_mode=req.runtime_mode,
            model_profile=req.model_profile,
            memory_mode=req.memory_mode,
            next_run_at=req.next_run_at,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    task = get_task(task_id)
    if not task:
        raise HTTPException(500, "Failed to create task")
    return _to_response(task)


@router.get("/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(task_id: int) -> ScheduledTaskResponse:
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return _to_response(task)


@router.patch("/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(task_id: int, req: UpdateScheduledTaskRequest) -> ScheduledTaskResponse:
    fields = req.model_dump(exclude_none=True)
    if "timezone" in fields:
        try:
            from patchquest.scheduler.scheduler import validate_timezone
            validate_timezone(fields["timezone"])
        except ValueError as e:
            raise HTTPException(400, str(e))

    update_task(task_id, **fields)
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return _to_response(task)


@router.delete("/tasks/{task_id}")
async def delete_scheduled_task(task_id: int) -> dict[str, str]:
    if not delete_task(task_id):
        raise HTTPException(404, "Task not found")
    return {"status": "deleted"}


@router.post("/tasks/{task_id}/pause")
async def pause_scheduled_task(task_id: int) -> dict[str, str]:
    if not pause_task(task_id):
        raise HTTPException(404, "Task not found or already paused")
    return {"status": "paused"}


@router.post("/tasks/{task_id}/resume")
async def resume_scheduled_task(task_id: int) -> dict[str, str]:
    if not resume_task(task_id):
        raise HTTPException(404, "Task not found")
    return {"status": "resumed"}


@router.post("/tasks/{task_id}/run-now")
async def run_scheduled_task_now(task_id: int) -> dict[str, str | None]:
    run_id = run_task_now(task_id)
    if not run_id:
        raise HTTPException(404, "Task not found")
    return {"status": "started", "run_id": run_id}


@router.get("/tasks/{task_id}/history", response_model=list[ScheduledRunHistoryResponse])
async def get_task_history(task_id: int) -> list[ScheduledRunHistoryResponse]:
    history = get_history(task_id)
    return [
        ScheduledRunHistoryResponse(
            id=h["id"],
            scheduled_task_id=h["scheduled_task_id"],
            run_id=h["run_id"],
            started_at=h["started_at"],
            finished_at=h.get("finished_at"),
            status=h["status"],
            message=h.get("message"),
            provider=h.get("provider"),
            model=h.get("model"),
            runtime_mode=h.get("runtime_mode"),
        )
        for h in history
    ]


def _to_response(task: dict) -> ScheduledTaskResponse:
    return ScheduledTaskResponse(
        id=task["id"],
        title=task["title"],
        task_prompt=task["task_prompt"],
        repo_path=task["repo_path"],
        schedule_type=task["schedule_type"],
        schedule_expr=task.get("schedule_expr"),
        timezone=task["timezone"],
        next_run_at=task.get("next_run_at"),
        last_run_at=task.get("last_run_at"),
        enabled=bool(task["enabled"]),
        status=task["status"],
        provider=task.get("provider") or "mock",
        model=task.get("model"),
        runtime_mode=task["runtime_mode"],
        model_profile=task.get("model_profile"),
        memory_mode=task["memory_mode"],
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )

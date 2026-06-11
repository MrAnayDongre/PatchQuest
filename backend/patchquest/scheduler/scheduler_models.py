"""Scheduler Pydantic schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateScheduledTaskRequest(BaseModel):
    title: str
    task_prompt: str
    repo_path: str
    schedule_type: str = "one_shot"
    schedule_expr: str | None = None
    timezone: str = "UTC"
    provider: str = "mock"
    model: str | None = None
    runtime_mode: str = "local"
    model_profile: str | None = None
    memory_mode: str = "repo"
    next_run_at: str | None = None


class UpdateScheduledTaskRequest(BaseModel):
    title: str | None = None
    task_prompt: str | None = None
    repo_path: str | None = None
    schedule_type: str | None = None
    schedule_expr: str | None = None
    timezone: str | None = None
    provider: str | None = None
    model: str | None = None
    runtime_mode: str | None = None
    model_profile: str | None = None
    memory_mode: str | None = None
    enabled: bool | None = None


class ScheduledTaskResponse(BaseModel):
    id: int
    title: str
    task_prompt: str
    repo_path: str
    schedule_type: str
    schedule_expr: str | None = None
    timezone: str
    next_run_at: str | None = None
    last_run_at: str | None = None
    enabled: bool
    status: str
    provider: str
    model: str | None = None
    runtime_mode: str
    model_profile: str | None = None
    memory_mode: str
    created_at: str
    updated_at: str


class ScheduledRunHistoryResponse(BaseModel):
    id: int
    scheduled_task_id: int
    run_id: str | None = None
    started_at: str
    finished_at: str | None = None
    status: str
    message: str | None = None
    provider: str | None = None
    model: str | None = None
    runtime_mode: str | None = None


class SchedulerStatusResponse(BaseModel):
    enabled: bool
    running: bool
    poll_interval_seconds: int
    active_tasks: int
    next_due: str | None = None

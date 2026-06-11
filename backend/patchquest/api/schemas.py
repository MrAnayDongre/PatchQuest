"""Pydantic schemas for API request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    repo_path: str
    task: str
    provider: str = "mock"
    model: str | None = None
    runtime_mode: str = "local"
    model_profile: str | None = None
    memory_mode: str | None = None
    interface_mode: str | None = None
    allow_network: bool = False
    dry_run: bool = False


class RunResponse(BaseModel):
    id: str
    repo_path: str
    task: str
    status: str
    current_phase: str | None = None
    provider: str = "mock"
    model: str | None = None
    runtime_mode: str = "local"
    model_profile: str | None = None
    memory_mode: str | None = None
    allow_network: bool = False
    dry_run: bool = False
    created_at: str
    updated_at: str
    completed_at: str | None = None


class ProviderInfo(BaseModel):
    name: str
    display_name: str
    api_key_env: str | None = None
    base_url: str | None = None
    default_model: str
    models: list[str] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    name: str
    available: bool
    key_set: bool
    error: str | None = None


class ProviderTestRequest(BaseModel):
    provider: str
    model: str | None = None


class RunEventResponse(BaseModel):
    id: int
    run_id: str
    type: str
    phase: str | None = None
    status: str | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None
    created_at: str


class ApprovalAction(BaseModel):
    approval_id: str
    approved: bool
    note: str | None = None


class ApprovalResponse(BaseModel):
    id: str
    run_id: str
    type: str
    command: str | None = None
    reason: str | None = None
    status: str
    created_at: str


class SettingsResponse(BaseModel):
    settings: dict[str, Any]


class UpdateSettingsRequest(BaseModel):
    settings: dict[str, Any]


class MemoryRecordResponse(BaseModel):
    id: int
    scope: str
    record_type: str
    key: str
    value: Any | None = None
    source_path: str | None = None
    status: str
    created_at: str
    updated_at: str


class RepoMapResponse(BaseModel):
    repo_path: str
    files: list[RepoFileInfo]
    total_files: int


class RepoFileInfo(BaseModel):
    path: str
    language: str | None = None
    size: int | None = None


class ReportResponse(BaseModel):
    run_id: str
    report_md: str | None = None
    diff_patch: str | None = None
    commands_log: str | None = None
    created_at: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


class PhaseStatusResponse(BaseModel):
    phase: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None

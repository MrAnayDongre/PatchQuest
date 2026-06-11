"""Report data models."""

from __future__ import annotations

from pydantic import BaseModel


class ReportRecord(BaseModel):
    run_id: str
    report_md: str | None = None
    diff_patch: str | None = None
    commands_log: str | None = None
    approvals_log: str | None = None
    events_jsonl: str | None = None
    created_at: str | None = None

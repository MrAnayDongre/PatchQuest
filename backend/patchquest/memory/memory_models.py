"""Memory data models."""

from __future__ import annotations

from pydantic import BaseModel


class MemoryRecord(BaseModel):
    id: int | None = None
    scope: str = "repo"
    record_type: str = "fact"
    key: str = ""
    value_json: str | None = None
    source_path: str | None = None
    git_commit: str | None = None
    file_hash: str | None = None
    confidence: float = 1.0
    status: str = "fresh"

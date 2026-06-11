"""Search data models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResultType(str, Enum):
    WEB = "web"
    NEWS = "news"
    DOCS = "docs"
    IMAGE = "image"
    VIDEO = "video"
    ISSUE = "issue"
    ADVISORY = "advisory"


class SearchResult(BaseModel):
    id: str
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    published_at: str | None = None
    score: float | None = None
    result_type: ResultType = ResultType.WEB
    raw_metadata: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    query: str
    provider: str
    results: list[SearchResult] = Field(default_factory=list)
    answer: str | None = None
    sources: list[str] = Field(default_factory=list)
    retrieved_at: str = ""
    cached: bool = False
    error: str | None = None


class SearchOptions(BaseModel):
    max_results: int = 8
    result_type: ResultType | None = None
    domains: list[str] | None = None
    force_refresh: bool = False
    country: str | None = None
    language: str | None = None

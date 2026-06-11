"""Application configuration loaded from YAML and environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ModelProfile(BaseModel):
    provider: str = "mock"
    model: str = "mock-default"
    base_url: str | None = None
    api_key_env: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.2


class ModelsConfig(BaseModel):
    intake: ModelProfile = Field(default_factory=lambda: ModelProfile(provider="mock", model="mock-intake"))
    planner: ModelProfile = Field(default_factory=lambda: ModelProfile(provider="mock", model="mock-planner"))
    analyst: ModelProfile = Field(default_factory=lambda: ModelProfile(provider="mock", model="mock-analyst"))
    coder: ModelProfile = Field(default_factory=lambda: ModelProfile(provider="mock", model="mock-coder"))
    reviewer: ModelProfile = Field(default_factory=lambda: ModelProfile(provider="mock", model="mock-reviewer"))
    security: ModelProfile = Field(default_factory=lambda: ModelProfile(provider="mock", model="mock-security"))
    memory_curator: ModelProfile = Field(default_factory=lambda: ModelProfile(provider="mock", model="mock-memory"))


class SafetyConfig(BaseModel):
    allow_network: bool = False
    allow_outside_repo: bool = False
    max_command_timeout: int = 60
    max_output_bytes: int = 1_000_000
    blocked_paths: list[str] = Field(default_factory=lambda: [
        "~/.ssh", "~/.aws", "~/.gcp", "~/.azure",
        "~/.config/gcloud", "~/.config/gh",
    ])


class MemoryConfig(BaseModel):
    mode: str = "repo"
    max_records: int = 10000


# --- Search config ---

class SearchProviderConfig(BaseModel):
    enabled: bool = False
    api_key_env: str | None = None
    search_engine_id_env: str | None = None
    base_url: str | None = None

    model_config = {"extra": "allow"}


class SearchProvidersConfig(BaseModel):
    brave: SearchProviderConfig = Field(default_factory=lambda: SearchProviderConfig(api_key_env="BRAVE_SEARCH_API_KEY"))
    tavily: SearchProviderConfig = Field(default_factory=lambda: SearchProviderConfig(api_key_env="TAVILY_API_KEY"))
    serper: SearchProviderConfig = Field(default_factory=lambda: SearchProviderConfig(api_key_env="SERPER_API_KEY"))
    serpapi: SearchProviderConfig = Field(default_factory=lambda: SearchProviderConfig(api_key_env="SERPAPI_API_KEY"))
    google_programmable: SearchProviderConfig = Field(default_factory=lambda: SearchProviderConfig(
        api_key_env="GOOGLE_SEARCH_API_KEY", search_engine_id_env="GOOGLE_SEARCH_ENGINE_ID"))
    duckduckgo: SearchProviderConfig = Field(default_factory=lambda: SearchProviderConfig(enabled=True))
    custom: SearchProviderConfig = Field(default_factory=SearchProviderConfig)

    model_config = {"extra": "allow"}


class SearchConfig(BaseModel):
    enabled: bool = True
    default_provider: str = "duckduckgo"
    cache_ttl_seconds: int = 3600
    max_results: int = 8
    allow_network: str = "ask"
    providers: SearchProvidersConfig = Field(default_factory=SearchProvidersConfig)


# --- Calendar config ---

class CalendarProviderConfig(BaseModel):
    enabled: bool = False
    export_path: str | None = None
    url_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    credentials_env: str | None = None
    client_id_env: str | None = None
    tenant_id_env: str | None = None

    model_config = {"extra": "allow"}


class CalendarProvidersConfig(BaseModel):
    local: CalendarProviderConfig = Field(default_factory=lambda: CalendarProviderConfig(enabled=True))
    ics: CalendarProviderConfig = Field(default_factory=lambda: CalendarProviderConfig(
        enabled=True, export_path=".patchquest/calendar/patchquest.ics"))
    caldav: CalendarProviderConfig = Field(default_factory=lambda: CalendarProviderConfig(
        url_env="CALDAV_URL", username_env="CALDAV_USERNAME", password_env="CALDAV_PASSWORD"))
    google: CalendarProviderConfig = Field(default_factory=lambda: CalendarProviderConfig(
        credentials_env="GOOGLE_CALENDAR_CREDENTIALS"))
    microsoft: CalendarProviderConfig = Field(default_factory=lambda: CalendarProviderConfig(
        client_id_env="MICROSOFT_CLIENT_ID", tenant_id_env="MICROSOFT_TENANT_ID"))

    model_config = {"extra": "allow"}


class CalendarConfig(BaseModel):
    enabled: bool = True
    default_provider: str = "local"
    default_calendar_id: str = "patchquest"
    create_events_for_scheduled_tasks: bool = True
    avoid_busy_times: bool = False
    reminder_minutes_before: int = 10
    providers: CalendarProvidersConfig = Field(default_factory=CalendarProvidersConfig)


# --- Repo intelligence config ---

class RepoIntelligenceConfig(BaseModel):
    parser: str = "tree_sitter"
    regex_fallback: bool = True
    supported_languages: list[str] = Field(default_factory=lambda: [
        "python", "javascript", "typescript", "rust", "c", "cpp", "go", "java",
    ])


class AppConfig(BaseModel):
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    repo_intelligence: RepoIntelligenceConfig = Field(default_factory=RepoIntelligenceConfig)
    db_path: str = "patchquest.db"
    host: str = "0.0.0.0"
    port: int = 8000


def load_config(config_path: str | None = None) -> AppConfig:
    if config_path is None:
        config_path = os.environ.get("PATCHQUEST_CONFIG", "config.yaml")

    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
        return AppConfig(**raw)

    return AppConfig()


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: AppConfig) -> None:
    global _config
    _config = config

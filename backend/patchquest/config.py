"""Application configuration loaded from YAML and environment."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
    # Extra environment variable names/patterns allowed into model-chosen commands.
    env_passthrough: list[str] = Field(default_factory=list)
    # Seconds a human has to answer an approval request before it is treated as denied.
    approval_timeout_seconds: int = 300
    # When non-empty, runs may only target repositories inside one of these directories.
    allowed_roots: list[str] = Field(default_factory=list)
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


class DockerConfig(BaseModel):
    image: str = "patchquest-sandbox:latest"
    memory: str = "2g"
    cpus: str = "2"
    pids_limit: int = 256
    network: bool = False  # off unless a human enables it; model-chosen commands never get a network by default
    timeout_seconds: int | None = None  # default: safety.max_command_timeout
    tmpfs_size: str = "512m"
    read_only_rootfs: bool = True


class RuntimeConfig(BaseModel):
    default: str = "local"
    docker: DockerConfig = Field(default_factory=DockerConfig)


class FailoverTarget(BaseModel):
    provider: str
    model: str | None = None
    base_url: str | None = None


class FailoverConfig(BaseModel):
    """When the model provider is down, rate limited or timing out, try these in order.

    Failover never silently changes what the user agreed to: it will not send data off this machine
    (``allow_cloud``) or lose a capability the run depends on (``allow_capability_downgrade``) unless
    explicitly allowed. Refusals are recorded as events.
    """

    chain: list[FailoverTarget] = Field(default_factory=list)
    allow_cloud: bool = False
    allow_capability_downgrade: bool = False


class AgentConfig(BaseModel):
    """Bounds on autonomous behaviour. Every loop in the engine reads its limit from here."""

    max_repair_rounds: int = 2
    max_test_commands: int = 3
    max_check_commands: int = 3
    context_budget_tokens: int = 6000
    # on_green: promote only when validation passed (or there was nothing to run); otherwise ask a human.
    # on_no_regression: also promote when the only remaining failures already fail without the patch.
    # always: promote regardless. never: leave the diff for review.
    promote_policy: str = "on_green"
    # Bounds on model usage per run (principle: every autonomous loop has limits).
    max_model_calls: int = 40
    # Total attempts to get a model's edits to apply (the first try plus feedback retries that quote the error).
    max_patch_attempts: int = 3
    max_total_tokens: int = 0  # 0 = unlimited
    max_retries: int = 10  # transient-failure retries per run, across every operation (0 = unlimited)
    max_wall_seconds: int = 3600  # active time per run, summed across resumes (0 = unlimited)
    max_commands: int = 60  # commands a run may execute (0 = unlimited)
    failover: FailoverConfig = Field(default_factory=FailoverConfig)
    # auto: ask for constrained (JSON-schema) output where the engine supports it, and stop asking for an
    #       endpoint+model once it misbehaves (e.g. loops on whitespace until the token cap).
    # off: never constrain. schema: always constrain, never fall back.
    structured_output: str = "auto"
    # One extra call, with the validation error, when a reply does not match the role's schema.
    format_repair_attempts: int = 1
    # Persist prompts and responses (needed for replay). Disable if prompts must not be stored.
    record_model_io: bool = True


class AppConfig(BaseModel):
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    repo_intelligence: RepoIntelligenceConfig = Field(default_factory=RepoIntelligenceConfig)
    # SQLite file. Unset: $PATCHQUEST_DB, else ./patchquest.db if it already exists (legacy), else
    # ~/.patchquest/patchquest.db. It is never created inside the repository you point a run at.
    db_path: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    # Name of the environment variable holding the API bearer token. Required when host is not loopback.
    api_token_env: str = "PATCHQUEST_API_TOKEN"
    # Extra Host header values accepted (the loopback names are always allowed).
    allowed_hosts: list[str] = Field(default_factory=list)


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
_run_overrides: ContextVar[dict[str, Any] | None] = ContextVar("patchquest_run_overrides", default=None)

# Only these sections may be overridden per run. Safety policy is deliberately absent: a run (or a fork
# of one) must never be able to weaken the command policy or approval rules by changing its own config.
OVERRIDABLE_SECTIONS = frozenset({"agent"})


def validate_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Check dotted overrides like ``{"agent.max_model_calls": 80}`` against the schema; returns them."""
    for key, value in overrides.items():
        section, _, name = key.partition(".")
        if section not in OVERRIDABLE_SECTIONS or not name or "." in name:
            raise ValueError(f"'{key}' cannot be overridden per run (allowed: agent.<setting>)")
        if name not in AgentConfig.model_fields:
            raise ValueError(f"unknown setting '{key}'")
        AgentConfig.model_validate({**AgentConfig().model_dump(), name: value})
    return dict(overrides)


def apply_overrides(base: AppConfig, overrides: dict[str, Any]) -> AppConfig:
    agent = base.agent.model_dump()
    agent.update({k.partition(".")[2]: v for k, v in validate_overrides(overrides).items()})
    return base.model_copy(update={"agent": AgentConfig(**agent)})


@contextmanager
def config_overrides(overrides: dict[str, Any] | None) -> Iterator[None]:
    """Layer per-run settings over the global config for everything running in this context
    (including threads started with ``asyncio.to_thread``). Concurrent runs do not see each other's."""
    token = _run_overrides.set(validate_overrides(overrides) if overrides else None)
    try:
        yield
    finally:
        _run_overrides.reset(token)


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    overrides = _run_overrides.get()
    return apply_overrides(_config, overrides) if overrides else _config


def set_config(config: AppConfig) -> None:
    global _config
    _config = config

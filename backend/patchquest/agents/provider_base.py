"""Provider contract.

A provider turns chat messages into text. What it *can* do is declared as
:class:`Capabilities`; callers ask for optional features (e.g. JSON-schema constrained output)
and the provider either honours the request or degrades **explicitly**, recording what it did in
``ProviderResponse.degraded`` so nothing is silently emulated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Capabilities:
    json_object: bool = False  # response_format={"type": "json_object"}
    json_schema: bool = False  # response_format={"type": "json_schema", ...} / guided decoding
    tools: bool = False
    streaming: bool = False
    vision: bool = False
    reasoning: bool = False
    max_context: int | None = None
    reports_usage: bool = True

    def with_updates(self, **changes: Any) -> Capabilities:
        return Capabilities(**{**self.__dict__, **changes})


@dataclass
class ProviderResponse:
    content: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    finish_reason: str = "stop"
    raw: Any = None
    latency_s: float | None = None
    attempts: int = 1
    # Requested features the provider could not honour, e.g. ["json_schema"].
    degraded: list[str] = field(default_factory=list)


@dataclass
class ModelConfig:
    provider: str = "mock"
    model: str = "mock-default"
    base_url: str | None = None
    api_key_env: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.2
    top_p: float | None = None
    timeout_seconds: float = 60.0
    # Optional hints from the catalogue/config: {"json_schema": True, ...}
    capability_hints: dict[str, Any] = field(default_factory=dict)


class ProviderBase(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
        response_format: dict | None = None,
    ) -> ProviderResponse:
        ...

    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        return True, ""

    def capabilities(self, config: ModelConfig) -> Capabilities:
        """Declared capabilities. Conservative by default; subclasses widen it."""
        return Capabilities()

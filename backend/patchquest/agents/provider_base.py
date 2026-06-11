"""Base provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderResponse:
    content: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    finish_reason: str = "stop"
    raw: Any = None


@dataclass
class ModelConfig:
    provider: str = "mock"
    model: str = "mock-default"
    base_url: str | None = None
    api_key_env: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.2
    top_p: float | None = None


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

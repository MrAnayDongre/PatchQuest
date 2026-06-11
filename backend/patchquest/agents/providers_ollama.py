"""Ollama provider."""

from __future__ import annotations

from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.providers_openai_compatible import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
    async def complete(self, messages, config: ModelConfig, response_format=None):
        if not config.base_url:
            config.base_url = "http://localhost:11434/v1"
        return await super().complete(messages, config, response_format)

    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        return True, ""

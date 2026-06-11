"""OpenRouter provider."""

from __future__ import annotations

from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.providers_openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    async def complete(self, messages, config: ModelConfig, response_format=None):
        if not config.base_url:
            config.base_url = "https://openrouter.ai/api/v1"
        if not config.api_key_env:
            config.api_key_env = "OPENROUTER_API_KEY"
        return await super().complete(messages, config, response_format)

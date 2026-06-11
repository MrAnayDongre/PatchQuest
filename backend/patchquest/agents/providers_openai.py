"""OpenAI provider."""

from __future__ import annotations

from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.providers_openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        if not config.api_key_env:
            return False, "api_key_env required for OpenAI"
        return True, ""

    async def complete(self, messages, config, response_format=None):
        if not config.base_url:
            config.base_url = "https://api.openai.com/v1"
        return await super().complete(messages, config, response_format)

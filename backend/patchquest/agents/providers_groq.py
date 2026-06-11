"""Groq provider."""

from __future__ import annotations

import os

from patchquest.agents.provider_base import ModelConfig, ProviderResponse
from patchquest.agents.providers_openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        env = config.api_key_env or "GROQ_API_KEY"
        if not os.environ.get(env, ""):
            return False, f"Environment variable {env} is not set. Get a key at https://console.groq.com"
        return True, ""

    async def complete(self, messages, config: ModelConfig, response_format=None):
        if not config.base_url:
            config.base_url = "https://api.groq.com/openai/v1"
        if not config.api_key_env:
            config.api_key_env = "GROQ_API_KEY"
        if not config.model or config.model.startswith("mock"):
            config.model = "llama-3.1-8b-instant"

        valid, err = self.validate_config(config)
        if not valid:
            raise RuntimeError(f"Groq provider configuration error: {err}")

        return await super().complete(messages, config, response_format)

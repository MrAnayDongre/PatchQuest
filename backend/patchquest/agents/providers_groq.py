"""Groq provider."""

from __future__ import annotations

import dataclasses
import os

from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.providers_openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        env = config.api_key_env or "GROQ_API_KEY"
        if not os.environ.get(env, ""):
            return False, f"Environment variable {env} is not set. Get a key at https://console.groq.com"
        return True, ""

    default_base_url = "https://api.groq.com/openai/v1"

    async def complete(self, messages, config: ModelConfig, response_format=None):
        # Work on a copy: callers' ModelConfig objects must not be mutated.
        config = dataclasses.replace(
            config,
            api_key_env=config.api_key_env or "GROQ_API_KEY",
            model="llama-3.1-8b-instant" if (not config.model or config.model.startswith("mock")) else config.model,
        )
        valid, err = self.validate_config(config)
        if not valid:
            raise RuntimeError(f"Groq provider configuration error: {err}")
        return await super().complete(messages, config, response_format)

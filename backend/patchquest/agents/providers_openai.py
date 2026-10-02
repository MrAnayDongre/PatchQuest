"""OpenAI provider."""

from __future__ import annotations

from patchquest.agents.provider_base import Capabilities, ModelConfig
from patchquest.agents.providers_openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    default_base_url = "https://api.openai.com/v1"

    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        if not config.api_key_env:
            return False, "api_key_env required for OpenAI"
        return True, ""

    def capabilities(self, config: ModelConfig) -> Capabilities:
        return Capabilities(json_object=True, json_schema=True)

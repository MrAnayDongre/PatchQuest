"""OpenAI-compatible provider (works with OpenAI, local LLMs, etc)."""

from __future__ import annotations

import json
import os
from typing import Any

from patchquest.agents.provider_base import ModelConfig, ProviderBase, ProviderResponse


class OpenAICompatibleProvider(ProviderBase):
    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
        response_format: dict | None = None,
    ) -> ProviderResponse:
        try:
            import httpx
        except ImportError:
            return ProviderResponse(content='{"error": "httpx not installed"}', finish_reason="error")

        api_key = ""
        if config.api_key_env:
            api_key = os.environ.get(config.api_key_env, "")

        base_url = config.base_url or "https://api.openai.com/v1"
        url = f"{base_url}/chat/completions"

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }
        if response_format:
            body["response_format"] = response_format

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        return ProviderResponse(
            content=choice["message"]["content"],
            usage=data.get("usage", {}),
            model=data.get("model", config.model),
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data,
        )

    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        if not config.base_url and not config.api_key_env:
            return False, "Either base_url or api_key_env required"
        return True, ""

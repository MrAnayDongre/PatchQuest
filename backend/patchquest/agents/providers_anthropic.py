"""Anthropic provider stub."""

from __future__ import annotations

import json
import os

from patchquest.agents.provider_base import ModelConfig, ProviderBase, ProviderResponse


class AnthropicProvider(ProviderBase):
    async def complete(self, messages, config: ModelConfig, response_format=None) -> ProviderResponse:
        try:
            import httpx
        except ImportError:
            return ProviderResponse(content='{"error": "httpx not installed"}', finish_reason="error")

        api_key = os.environ.get(config.api_key_env or "ANTHROPIC_API_KEY", "")
        if not api_key:
            return ProviderResponse(content='{"error": "No API key configured"}', finish_reason="error")

        system_msg = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                user_messages.append(m)

        body = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": user_messages,
        }
        if system_msg:
            body["system"] = system_msg

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data["content"][0]["text"] if data.get("content") else ""
        return ProviderResponse(
            content=content,
            usage=data.get("usage", {}),
            model=data.get("model", config.model),
            finish_reason=data.get("stop_reason", "stop"),
        )

    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        if not config.api_key_env:
            return False, "api_key_env required for Anthropic"
        return True, ""

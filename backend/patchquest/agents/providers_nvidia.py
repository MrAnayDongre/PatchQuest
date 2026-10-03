"""NVIDIA NIM / Build provider using the OpenAI Responses API."""

from __future__ import annotations

import logging
import os
from typing import Any

from patchquest.agents.provider_base import ModelConfig, ProviderBase, ProviderResponse

logger = logging.getLogger(__name__)

_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "openai/gpt-oss-120b"

def _messages_to_responses_input(messages: list[dict[str, str]]) -> str:
    """Convert chat-style messages into a single Responses API input string.

    Preserves system instructions, user task, and expected output format
    in a clearly delimited structure.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"[System Instructions]\n{content}")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}")
        else:
            parts.append(f"[User]\n{content}")
    return "\n\n".join(parts)


def _extract_output_text(data: dict[str, Any]) -> str:
    """Robustly extract user-visible text from a Responses API response.

    Only includes output_text / message content marked as output.
    Never includes reasoning_text or internal reasoning blocks.
    """
    if data.get("output_text"):
        return data["output_text"]

    output_items = data.get("output", [])
    texts: list[str] = []
    for item in output_items:
        if isinstance(item, str):
            texts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")
        if item_type in ("reasoning", "reasoning_text"):
            continue
        if item_type == "message":
            for content_part in item.get("content", []):
                if not isinstance(content_part, dict):
                    continue
                part_type = content_part.get("type", "")
                if part_type in ("reasoning", "reasoning_text"):
                    continue
                if part_type == "output_text":
                    texts.append(content_part.get("text", ""))
                elif part_type in ("", "text") and content_part.get("text"):
                    texts.append(content_part["text"])
        elif item.get("text"):
            texts.append(item["text"])

    if texts:
        return "\n".join(texts)

    return str(data.get("output", data.get("choices", [{}])))


def _redact_key(message: str, api_key: str) -> str:
    if api_key and api_key in message:
        return message.replace(api_key, "***REDACTED***")
    return message


class NvidiaProvider(ProviderBase):
    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        env = config.api_key_env or "NVIDIA_API_KEY"
        if not os.environ.get(env, ""):
            return False, (
                f"Environment variable {env} is not set. "
                "Get a key at https://build.nvidia.com"
            )
        return True, ""

    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
        response_format: dict | None = None,
    ) -> ProviderResponse:
        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx is required for NVIDIA provider but is not installed") from None

        if not config.base_url:
            config.base_url = _BASE_URL
        if not config.api_key_env:
            config.api_key_env = "NVIDIA_API_KEY"
        if not config.model or config.model.startswith("mock"):
            config.model = _DEFAULT_MODEL

        valid, err = self.validate_config(config)
        if not valid:
            raise RuntimeError(f"NVIDIA provider configuration error: {err}")

        api_key = os.environ.get(config.api_key_env, "")
        url = f"{config.base_url.rstrip('/')}/responses"
        input_text = _messages_to_responses_input(messages)

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {
            "model": config.model,
            "input": input_text,
            "max_output_tokens": config.max_tokens,
            "temperature": config.temperature if config.temperature != 0.2 else 1,
            "top_p": config.top_p if config.top_p is not None else 1,
            "stream": False,
        }

        # One attempt: whether and when to retry is decided centrally (runtime.retry), from the typed
        # failure, so retries do not multiply across layers.
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body, headers=headers)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise httpx.HTTPStatusError(_redact_key(str(exc), api_key), request=exc.request, response=exc.response) from None
        data = resp.json()

        content = _extract_output_text(data)
        usage_data = data.get("usage", {})
        usage = {}
        if isinstance(usage_data, dict):
            if "input_tokens" in usage_data:
                usage["prompt_tokens"] = usage_data["input_tokens"]
            if "output_tokens" in usage_data:
                usage["completion_tokens"] = usage_data["output_tokens"]
            if "total_tokens" in usage_data:
                usage["total_tokens"] = usage_data["total_tokens"]

        return ProviderResponse(
            content=content,
            usage=usage,
            model=data.get("model", config.model),
            finish_reason=data.get("status", "completed"),
            raw=data,
        )

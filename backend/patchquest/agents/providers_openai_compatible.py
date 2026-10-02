"""OpenAI-compatible chat-completions provider (OpenAI, vLLM, SGLang, llama.cpp, LM Studio, Ollama, ...).

Structured output: when the caller passes a ``response_format`` (``json_schema`` preferred,
``json_object`` as the fallback) and the endpoint rejects it with a client error, the request is
retried once without it and the response is marked ``degraded=["json_schema"]``. That decision is
remembered per ``(base_url, model)`` so a known-unsupported feature is not retried on every call.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from patchquest.agents.provider_base import Capabilities, ModelConfig, ProviderBase, ProviderResponse

# (base_url, model, feature) -> unsupported, learned at runtime.
_UNSUPPORTED: set[tuple[str, str, str]] = set()


def client_factory(timeout: float) -> httpx.AsyncClient:
    """Indirection so tests (and callers needing proxies/mTLS) can supply their own client."""
    return httpx.AsyncClient(timeout=timeout)


def _feature_of(response_format: dict | None) -> str | None:
    if not response_format:
        return None
    return "json_schema" if response_format.get("type") == "json_schema" else "json_object"


def _is_feature_rejection(resp: httpx.Response) -> bool:
    """A 400/422 that complains about response_format/schema/grammar, not about auth or quota."""
    if resp.status_code not in (400, 422):
        return False
    text = resp.text.lower()
    return any(w in text for w in ("response_format", "json_schema", "schema", "grammar", "guided", "json_object"))


class OpenAICompatibleProvider(ProviderBase):
    default_base_url: str | None = None

    def capabilities(self, config: ModelConfig) -> Capabilities:
        hints = config.capability_hints or {}
        return Capabilities(
            json_object=bool(hints.get("json_object", False)),
            json_schema=bool(hints.get("json_schema", False)),
            max_context=hints.get("max_context"),
        )

    def _endpoint(self, config: ModelConfig) -> str:
        base = (config.base_url or self.default_base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions"

    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
        response_format: dict | None = None,
    ) -> ProviderResponse:
        api_key = os.environ.get(config.api_key_env, "") if config.api_key_env else ""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }
        if config.top_p is not None:
            body["top_p"] = config.top_p

        url = self._endpoint(config)
        feature = _feature_of(response_format)
        key = (url, config.model, feature or "")
        degraded: list[str] = []
        if response_format and key not in _UNSUPPORTED:
            body["response_format"] = response_format
        elif feature:
            degraded.append(feature)

        started = time.monotonic()
        attempts = 1
        async with client_factory(config.timeout_seconds) as client:
            resp = await client.post(url, json=body, headers=headers)
            if "response_format" in body and _is_feature_rejection(resp):
                _UNSUPPORTED.add(key)
                degraded.append(feature or "response_format")
                body.pop("response_format")
                attempts += 1
                resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        return ProviderResponse(
            content=choice["message"].get("content") or "",
            usage=data.get("usage") or {},
            model=data.get("model", config.model),
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data,
            latency_s=round(time.monotonic() - started, 4),
            attempts=attempts,
            degraded=degraded,
        )

    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        if not config.base_url and not self.default_base_url and not config.api_key_env:
            return False, "Either base_url or api_key_env required"
        return True, ""


class LocalEngineProvider(OpenAICompatibleProvider):
    """Base for locally served OpenAI-compatible engines (no API key required)."""

    engine: str = "local"

    def capabilities(self, config: ModelConfig) -> Capabilities:
        hints = {"json_schema": True, "json_object": True, **(config.capability_hints or {})}
        return Capabilities(json_object=bool(hints["json_object"]), json_schema=bool(hints["json_schema"]),
                            max_context=hints.get("max_context"))

    def validate_config(self, config: ModelConfig) -> tuple[bool, str]:
        return True, ""


class VLLMProvider(LocalEngineProvider):
    engine, default_base_url = "vllm", "http://localhost:8000/v1"


class SGLangProvider(LocalEngineProvider):
    engine, default_base_url = "sglang", "http://localhost:30000/v1"


class LlamaCppProvider(LocalEngineProvider):
    engine, default_base_url = "llamacpp", "http://localhost:8080/v1"


class LMStudioProvider(LocalEngineProvider):
    engine, default_base_url = "lmstudio", "http://localhost:1234/v1"

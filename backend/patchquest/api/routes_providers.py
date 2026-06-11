"""LLM provider discovery, status, and testing endpoints."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from patchquest.api.schemas import ProviderInfo, ProviderStatus, ProviderTestRequest

router = APIRouter(prefix="/api/providers", tags=["providers"])

PROVIDER_CATALOG: list[dict[str, Any]] = [
    {
        "name": "mock",
        "display_name": "Mock (Demo)",
        "api_key_env": None,
        "base_url": None,
        "default_model": "mock-default",
        "models": ["mock-default"],
    },
    {
        "name": "groq",
        "display_name": "Groq",
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.1-8b-instant",
        "models": [
            "llama-3.1-8b-instant",
            "llama-3.1-70b-versatile",
            "llama3-8b-8192",
            "llama3-70b-8192",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
    },
    {
        "name": "openai",
        "display_name": "OpenAI",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    },
    {
        "name": "anthropic",
        "display_name": "Anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-3-haiku-20240307",
        "models": ["claude-3-haiku-20240307", "claude-3-sonnet-20240229", "claude-3-opus-20240229"],
    },
    {
        "name": "ollama",
        "display_name": "Ollama (Local)",
        "api_key_env": None,
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "models": ["llama3", "codellama", "mistral", "phi3"],
    },
    {
        "name": "nvidia",
        "display_name": "NVIDIA NIM / Build",
        "api_key_env": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "openai/gpt-oss-120b",
        "models": [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        ],
    },
    {
        "name": "openrouter",
        "display_name": "OpenRouter",
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3-8b-instruct",
        "models": ["meta-llama/llama-3-8b-instruct", "mistralai/mixtral-8x7b-instruct"],
    },
    {
        "name": "openai_compatible",
        "display_name": "OpenAI-Compatible",
        "api_key_env": None,
        "base_url": None,
        "default_model": "custom",
        "models": [],
    },
]


@router.get("", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    return [ProviderInfo(**p) for p in PROVIDER_CATALOG]


@router.get("/status", response_model=list[ProviderStatus])
async def provider_status() -> list[ProviderStatus]:
    results: list[ProviderStatus] = []
    for p in PROVIDER_CATALOG:
        key_env = p.get("api_key_env")
        key_set = bool(os.environ.get(key_env, "")) if key_env else True
        available = key_set if p["name"] != "mock" else True
        results.append(ProviderStatus(
            name=p["name"],
            available=available,
            key_set=key_set,
        ))
    return results


@router.post("/test", response_model=ProviderStatus)
async def test_provider(req: ProviderTestRequest) -> ProviderStatus:
    from patchquest.agents.provider_base import ModelConfig
    from patchquest.agents.provider_registry import get_provider

    catalog_entry = next((p for p in PROVIDER_CATALOG if p["name"] == req.provider), None)
    if not catalog_entry:
        return ProviderStatus(name=req.provider, available=False, key_set=False,
                              error=f"Unknown provider: {req.provider}")

    key_env = catalog_entry.get("api_key_env")
    key_set = bool(os.environ.get(key_env, "")) if key_env else True
    if not key_set:
        env_name = key_env or "API_KEY"
        return ProviderStatus(name=req.provider, available=False, key_set=False,
                              error=f"Environment variable {env_name} is not set")

    if req.provider == "mock":
        return ProviderStatus(name="mock", available=True, key_set=True)

    model = req.model or catalog_entry["default_model"]
    config = ModelConfig(
        provider=req.provider,
        model=model,
        base_url=catalog_entry.get("base_url"),
        api_key_env=key_env,
        max_tokens=32,
        temperature=0.0,
    )
    provider = get_provider(req.provider)
    try:
        resp = await provider.complete(
            [{"role": "user", "content": "Say OK"}],
            config,
        )
        if resp.finish_reason == "error":
            return ProviderStatus(name=req.provider, available=False, key_set=key_set,
                                  error=f"Provider returned error: {resp.content[:200]}")
        return ProviderStatus(name=req.provider, available=True, key_set=key_set)
    except Exception as exc:
        err_msg = str(exc)
        if key_env and os.environ.get(key_env, ""):
            err_msg = err_msg.replace(os.environ[key_env], "***REDACTED***")
        return ProviderStatus(name=req.provider, available=False, key_set=key_set,
                              error=err_msg[:300])

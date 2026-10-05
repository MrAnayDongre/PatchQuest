"""LLM provider discovery, status, and testing endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from patchquest.api.auth import require
from patchquest.api.schemas import ProviderInfo, ProviderStatus, ProviderTestRequest
from patchquest.domain.identity import Permission
from patchquest.providers.catalog import PROVIDER_CATALOG

router = APIRouter(prefix="/api/providers", tags=["providers"])



@router.get("", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    return [ProviderInfo(**p) for p in PROVIDER_CATALOG]


@router.get("/engines")
async def engines() -> list[dict]:
    from patchquest.providers.engines import engine_report

    return await engine_report()


@router.get("/health")
async def provider_health() -> list[dict]:
    from patchquest.providers import health

    return health.snapshot()


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


@router.post("/test", response_model=ProviderStatus, dependencies=[Depends(require(Permission.SETTINGS_WRITE))])
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

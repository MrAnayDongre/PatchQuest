"""Search API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from patchquest.api.auth import CurrentPrincipal
from patchquest.domain.failures import PatchQuestError
from patchquest.domain.identity import Permission

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchQueryRequest(BaseModel):
    query: str
    provider: str | None = None
    max_results: int = 8
    result_type: str | None = None
    domains: list[str] | None = None
    force_refresh: bool = False
    workspace_id: str = "ws_local"  # whose policy decides which search hosts may be contacted


@router.get("/providers")
async def list_providers():
    from patchquest.search.search_registry import get_provider_status
    return get_provider_status()


@router.get("/status")
async def search_status():
    from patchquest.config import get_config
    from patchquest.search.search_registry import get_provider_status
    from patchquest.search.search_service import get_cache_stats
    cfg = get_config()
    return {
        "enabled": cfg.search.enabled,
        "default_provider": cfg.search.default_provider,
        "allow_network": cfg.search.allow_network,
        "providers": get_provider_status(),
        "cache": get_cache_stats(),
    }


@router.post("/query")
async def search_query(req: SearchQueryRequest, principal: CurrentPrincipal):
    from patchquest.config import get_config
    from patchquest.runtime import policy as policy_runtime
    cfg = get_config()
    if not principal.can(Permission.RUN_READ, req.workspace_id):
        raise HTTPException(status_code=404, detail="Not found")
    if not cfg.search.enabled:
        raise HTTPException(status_code=400, detail="Search is disabled")

    from patchquest.search.search_models import SearchOptions
    from patchquest.search.search_service import search

    options = SearchOptions(
        max_results=req.max_results,
        force_refresh=req.force_refresh,
        domains=req.domains,
    )
    try:
        result = await search(
            query=req.query,
            provider_name=req.provider,
            options=options,
            cache_ttl=cfg.search.cache_ttl_seconds,
            policy_chain=policy_runtime.chain_for(workspace_id=req.workspace_id),
        )
        return result.model_dump()
    except PatchQuestError as e:
        raise HTTPException(status_code=403, detail=e.detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/cache")
async def get_cache():
    from patchquest.search.search_service import get_cache_stats
    return get_cache_stats()


@router.delete("/cache")
async def clear_cache():
    from patchquest.search.search_service import clear_cache
    deleted = clear_cache()
    return {"deleted": deleted}

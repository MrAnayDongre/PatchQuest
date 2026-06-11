"""Search service — caching, rate limiting, secret redaction, provider dispatch."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from patchquest.database import get_db, now_iso
from patchquest.search.search_models import SearchOptions, SearchResponse
from patchquest.search.search_registry import get_search_provider

logger = logging.getLogger(__name__)

_rate_limits: dict[str, float] = {}
RATE_LIMIT_SECONDS = 2.0


def _cache_key(provider: str, query: str, options: SearchOptions) -> str:
    blob = json.dumps({"p": provider, "q": query, "mr": options.max_results,
                        "rt": options.result_type, "d": options.domains}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _get_cached(cache_key: str, ttl: int) -> SearchResponse | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT results_json, retrieved_at FROM search_cache WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now_iso()),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["results_json"])
        resp = SearchResponse(**data)
        resp.cached = True
        return resp
    except Exception:
        return None


def _store_cache(cache_key: str, query: str, provider: str, response: SearchResponse, ttl: int) -> None:
    now = now_iso()
    expires = datetime.now(timezone.utc).isoformat()
    from datetime import timedelta
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO search_cache
               (cache_key, query, provider, results_json, retrieved_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cache_key, query, provider, response.model_dump_json(), now, expires),
        )


def _check_rate_limit(provider: str) -> None:
    last = _rate_limits.get(provider, 0)
    now = time.monotonic()
    if now - last < RATE_LIMIT_SECONDS:
        wait = RATE_LIMIT_SECONDS - (now - last)
        import asyncio
        raise RuntimeError(f"Rate limited: wait {wait:.1f}s before searching with {provider}")
    _rate_limits[provider] = now


def _redact_secrets(response: SearchResponse) -> SearchResponse:
    try:
        from patchquest.tools.secret_guard import redact_secrets
        for result in response.results:
            result.snippet = redact_secrets(result.snippet)
            result.title = redact_secrets(result.title)
        if response.answer:
            response.answer = redact_secrets(response.answer)
    except ImportError:
        pass
    return response


async def search(
    query: str,
    provider_name: str | None = None,
    options: SearchOptions | None = None,
    cache_ttl: int = 3600,
) -> SearchResponse:
    from patchquest.config import get_config
    cfg = get_config()
    search_cfg = getattr(cfg, "search", None)

    if provider_name is None:
        provider_name = getattr(search_cfg, "default_provider", "duckduckgo") if search_cfg else "duckduckgo"

    opts = options or SearchOptions()

    if not opts.force_refresh:
        key = _cache_key(provider_name, query, opts)
        cached = _get_cached(key, cache_ttl)
        if cached:
            logger.info("Search cache hit for %s via %s", query, provider_name)
            return cached

    _check_rate_limit(provider_name)

    provider_cfg = {}
    if search_cfg and hasattr(search_cfg, "providers"):
        pcfg = getattr(search_cfg.providers, provider_name, None)
        if pcfg:
            provider_cfg = {k: v for k, v in pcfg.model_dump().items() if k != "enabled" and v is not None}

    provider = get_search_provider(provider_name, **provider_cfg)

    try:
        response = await provider.search(query, opts)
    except Exception as e:
        logger.error("Search failed for provider %s: %s", provider_name, e)
        return SearchResponse(
            query=query, provider=provider_name, retrieved_at=now_iso(),
            error=str(e),
        )

    response = _redact_secrets(response)

    key = _cache_key(provider_name, query, opts)
    _store_cache(key, query, provider_name, response, cache_ttl)

    return response


def clear_cache() -> int:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM search_cache")
        return cursor.rowcount


def get_cache_stats() -> dict:
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM search_cache").fetchone()["c"]
        expired = conn.execute(
            "SELECT COUNT(*) as c FROM search_cache WHERE expires_at <= ?", (now_iso(),)
        ).fetchone()["c"]
    return {"total": total, "expired": expired, "active": total - expired}

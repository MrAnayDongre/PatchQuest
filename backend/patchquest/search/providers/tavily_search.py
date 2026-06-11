"""Tavily Search API provider."""

from __future__ import annotations

import hashlib
import logging

from patchquest.search.search_models import SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_provider_base import SearchProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tavily.com/search"


class TavilySearchProvider(SearchProvider):
    name = "tavily"
    requires_api_key = True

    def __init__(self, api_key_env: str = "TAVILY_API_KEY", base_url: str = BASE_URL):
        self.api_key_env = api_key_env
        self.base_url = base_url

    def validate_config(self) -> tuple[bool, str]:
        key = self._get_env(self.api_key_env)
        if not key:
            return False, f"Missing env var {self.api_key_env}"
        return True, "ok"

    async def search(self, query: str, options: SearchOptions | None = None) -> SearchResponse:
        import httpx

        opts = options or SearchOptions()
        api_key = self._require_env(self.api_key_env)

        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": opts.max_results,
            "include_answer": True,
            "include_raw_content": False,
            "search_depth": "basic",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self.base_url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append(SearchResult(
                id=hashlib.sha256(item.get("url", "").encode()).hexdigest()[:16],
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source=self.name,
                score=item.get("score"),
            ))

        from patchquest.database import now_iso
        return SearchResponse(
            query=query, provider=self.name, results=results,
            answer=data.get("answer"),
            retrieved_at=now_iso(), sources=[r.url for r in results],
        )

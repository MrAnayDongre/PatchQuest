"""SerpApi search provider."""

from __future__ import annotations

import hashlib
import logging

from patchquest.search.search_models import SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_provider_base import SearchProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://serpapi.com/search"


class SerpApiSearchProvider(SearchProvider):
    name = "serpapi"
    requires_api_key = True

    def __init__(self, api_key_env: str = "SERPAPI_API_KEY", base_url: str = BASE_URL, engine: str = "google"):
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.engine = engine

    def validate_config(self) -> tuple[bool, str]:
        key = self._get_env(self.api_key_env)
        if not key:
            return False, f"Missing env var {self.api_key_env}"
        return True, "ok"

    async def search(self, query: str, options: SearchOptions | None = None) -> SearchResponse:
        import httpx

        opts = options or SearchOptions()
        api_key = self._require_env(self.api_key_env)

        params = {
            "q": query,
            "api_key": api_key,
            "engine": self.engine,
            "num": opts.max_results,
            "output": "json",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("organic_results", []):
            results.append(SearchResult(
                id=hashlib.sha256(item.get("link", "").encode()).hexdigest()[:16],
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                source=self.name,
                score=item.get("position"),
            ))

        from patchquest.database import now_iso
        return SearchResponse(
            query=query, provider=self.name, results=results,
            answer=data.get("answer_box", {}).get("answer") if isinstance(data.get("answer_box"), dict) else None,
            retrieved_at=now_iso(), sources=[r.url for r in results],
        )

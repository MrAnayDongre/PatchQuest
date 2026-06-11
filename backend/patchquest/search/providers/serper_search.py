"""Serper (serper.dev) search provider."""

from __future__ import annotations

import hashlib
import logging

from patchquest.search.search_models import SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_provider_base import SearchProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://google.serper.dev/search"


class SerperSearchProvider(SearchProvider):
    name = "serper"
    requires_api_key = True

    def __init__(self, api_key_env: str = "SERPER_API_KEY", base_url: str = BASE_URL):
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

        payload = {"q": query, "num": opts.max_results}
        if opts.country:
            payload["gl"] = opts.country

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self.base_url, json=payload,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("organic", []):
            results.append(SearchResult(
                id=hashlib.sha256(item.get("link", "").encode()).hexdigest()[:16],
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                source=self.name,
            ))

        from patchquest.database import now_iso
        return SearchResponse(
            query=query, provider=self.name, results=results,
            answer=data.get("answerBox", {}).get("answer") if isinstance(data.get("answerBox"), dict) else None,
            retrieved_at=now_iso(), sources=[r.url for r in results],
        )

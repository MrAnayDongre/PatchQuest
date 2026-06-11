"""Google Programmable Search Engine (Custom Search JSON API) provider."""

from __future__ import annotations

import hashlib
import logging

from patchquest.search.search_models import SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_provider_base import SearchProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/customsearch/v1"


class GoogleProgrammableSearchProvider(SearchProvider):
    name = "google_programmable"
    requires_api_key = True

    def __init__(
        self,
        api_key_env: str = "GOOGLE_SEARCH_API_KEY",
        search_engine_id_env: str = "GOOGLE_SEARCH_ENGINE_ID",
        base_url: str = BASE_URL,
    ):
        self.api_key_env = api_key_env
        self.search_engine_id_env = search_engine_id_env
        self.base_url = base_url

    def validate_config(self) -> tuple[bool, str]:
        key = self._get_env(self.api_key_env)
        cx = self._get_env(self.search_engine_id_env)
        if not key:
            return False, f"Missing env var {self.api_key_env}"
        if not cx:
            return False, f"Missing env var {self.search_engine_id_env}"
        return True, "ok"

    async def search(self, query: str, options: SearchOptions | None = None) -> SearchResponse:
        import httpx

        opts = options or SearchOptions()
        api_key = self._require_env(self.api_key_env)
        cx = self._require_env(self.search_engine_id_env)

        params = {"q": query, "key": api_key, "cx": cx, "num": min(opts.max_results, 10)}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("items", []):
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
            retrieved_at=now_iso(), sources=[r.url for r in results],
        )

"""DuckDuckGo Instant Answer API — no API key required, best-effort fallback.

Uses the official DuckDuckGo Instant Answer API (api.duckduckgo.com) which
returns structured instant-answer data. This is intentionally limited compared
to paid providers: it returns topic summaries and related topics rather than
full web results. It exists so PatchQuest can do basic lookups without any
API keys configured.
"""

from __future__ import annotations

import hashlib
import logging

from patchquest.search.search_models import SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_provider_base import SearchProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.duckduckgo.com/"


class DuckDuckGoSearchProvider(SearchProvider):
    name = "duckduckgo"
    requires_api_key = False

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url

    def validate_config(self) -> tuple[bool, str]:
        return True, "ok (no API key required, best-effort instant answers only)"

    async def search(self, query: str, options: SearchOptions | None = None) -> SearchResponse:
        import httpx

        opts = options or SearchOptions()

        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []

        if data.get("AbstractText"):
            results.append(SearchResult(
                id=hashlib.sha256(data.get("AbstractURL", "").encode()).hexdigest()[:16],
                title=data.get("Heading", query),
                url=data.get("AbstractURL", ""),
                snippet=data.get("AbstractText", ""),
                source=self.name,
            ))

        for topic in data.get("RelatedTopics", [])[:opts.max_results]:
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append(SearchResult(
                    id=hashlib.sha256(topic["FirstURL"].encode()).hexdigest()[:16],
                    title=topic.get("Text", "")[:120],
                    url=topic["FirstURL"],
                    snippet=topic.get("Text", ""),
                    source=self.name,
                ))

        from patchquest.database import now_iso
        return SearchResponse(
            query=query, provider=self.name, results=results[:opts.max_results],
            answer=data.get("AbstractText") or None,
            retrieved_at=now_iso(), sources=[r.url for r in results],
        )

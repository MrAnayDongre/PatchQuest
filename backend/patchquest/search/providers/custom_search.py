"""Custom search endpoint provider — user-defined search API."""

from __future__ import annotations

import hashlib
import logging

from patchquest.search.search_models import SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_provider_base import SearchProvider

logger = logging.getLogger(__name__)


class CustomSearchProvider(SearchProvider):
    name = "custom"
    requires_api_key = False

    def __init__(
        self,
        base_url: str = "",
        method: str = "GET",
        api_key_env: str | None = None,
        query_param: str = "q",
        results_path: str = "results",
        title_field: str = "title",
        url_field: str = "url",
        snippet_field: str = "snippet",
        headers_env: str | None = None,
    ):
        self.base_url = base_url
        self.method = method.upper()
        self.api_key_env = api_key_env
        self.query_param = query_param
        self.results_path = results_path
        self.title_field = title_field
        self.url_field = url_field
        self.snippet_field = snippet_field
        self.headers_env = headers_env

    def validate_config(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "base_url not configured"
        if self.api_key_env and not self._get_env(self.api_key_env):
            return False, f"Missing env var {self.api_key_env}"
        return True, "ok"

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key_env:
            key = self._get_env(self.api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        if self.headers_env:
            import json
            import os
            raw = os.environ.get(self.headers_env, "")
            if raw:
                try:
                    extra = json.loads(raw)
                    if isinstance(extra, dict):
                        headers.update(extra)
                except (json.JSONDecodeError, TypeError):
                    pass
        return headers

    def _extract_results(self, data: dict, path: str) -> list:
        parts = path.split(".")
        obj = data
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part, [])
            else:
                return []
        return obj if isinstance(obj, list) else []

    async def search(self, query: str, options: SearchOptions | None = None) -> SearchResponse:
        import httpx

        opts = options or SearchOptions()
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=15) as client:
            if self.method == "POST":
                resp = await client.post(
                    self.base_url, json={self.query_param: query, "max_results": opts.max_results},
                    headers=headers,
                )
            else:
                resp = await client.get(
                    self.base_url, params={self.query_param: query, "max_results": str(opts.max_results)},
                    headers=headers,
                )
            resp.raise_for_status()
            data = resp.json()

        raw_items = self._extract_results(data, self.results_path)
        results = []
        for item in raw_items[:opts.max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get(self.url_field, ""))
            results.append(SearchResult(
                id=hashlib.sha256(url.encode()).hexdigest()[:16],
                title=str(item.get(self.title_field, "")),
                url=url,
                snippet=str(item.get(self.snippet_field, "")),
                source=self.name,
            ))

        from patchquest.database import now_iso
        return SearchResponse(
            query=query, provider=self.name, results=results,
            retrieved_at=now_iso(), sources=[r.url for r in results],
        )

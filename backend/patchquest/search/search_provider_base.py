"""Abstract base class for search providers."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from patchquest.search.search_models import SearchOptions, SearchResponse


class SearchProvider(ABC):
    name: str = "base"
    requires_api_key: bool = True

    @abstractmethod
    async def search(self, query: str, options: SearchOptions | None = None) -> SearchResponse:
        ...

    async def search_news(self, query: str, options: SearchOptions | None = None) -> SearchResponse:
        return await self.search(query, options)

    def validate_config(self) -> tuple[bool, str]:
        return True, "ok"

    def _get_env(self, env_var: str | None) -> str | None:
        if not env_var:
            return None
        return os.environ.get(env_var)

    def _require_env(self, env_var: str) -> str:
        val = os.environ.get(env_var, "")
        if not val:
            raise ValueError(f"Missing environment variable: {env_var}")
        return val

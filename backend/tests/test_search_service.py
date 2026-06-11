"""Tests for search service — caching, redaction, rate limits."""

import pytest

from patchquest.search.search_models import SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_service import _cache_key, _redact_secrets


def test_cache_key_deterministic():
    opts = SearchOptions(max_results=5)
    k1 = _cache_key("brave", "test query", opts)
    k2 = _cache_key("brave", "test query", opts)
    assert k1 == k2


def test_cache_key_differs_by_provider():
    opts = SearchOptions()
    k1 = _cache_key("brave", "test", opts)
    k2 = _cache_key("tavily", "test", opts)
    assert k1 != k2


def test_cache_key_differs_by_query():
    opts = SearchOptions()
    k1 = _cache_key("brave", "query1", opts)
    k2 = _cache_key("brave", "query2", opts)
    assert k1 != k2


def test_redact_secrets_passthrough():
    resp = SearchResponse(
        query="test", provider="mock",
        results=[SearchResult(id="1", title="Safe Title", url="https://example.com", snippet="Safe content")],
    )
    redacted = _redact_secrets(resp)
    assert redacted.results[0].title == "Safe Title"
    assert redacted.results[0].snippet == "Safe content"

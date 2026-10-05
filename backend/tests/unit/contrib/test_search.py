"""Web-search models, provider registry and service (contrib, outside the core coding-agent path).
"""

import pytest

from patchquest.search.search_models import ResultType, SearchOptions, SearchResponse, SearchResult
from patchquest.search.search_registry import (
    get_provider_status,
    get_search_provider,
    list_search_providers,
)
from patchquest.search.search_service import _cache_key, _redact_secrets

# ======================================================================
# Models
# ======================================================================

def test_search_result_defaults():
    r = SearchResult(id="1", title="Test", url="https://example.com")
    assert r.result_type == ResultType.WEB
    assert r.snippet == ""
    assert r.score is None


def test_search_response_defaults():
    resp = SearchResponse(query="test", provider="mock")
    assert resp.results == []
    assert resp.cached is False
    assert resp.error is None


def test_search_options_defaults():
    opts = SearchOptions()
    assert opts.max_results == 8
    assert opts.force_refresh is False
    assert opts.domains is None


def test_result_type_values():
    assert ResultType.WEB == "web"
    assert ResultType.NEWS == "news"
    assert ResultType.ADVISORY == "advisory"


# ======================================================================
# Registry
# ======================================================================

def test_list_providers_includes_all():
    providers = list_search_providers()
    assert "brave" in providers
    assert "tavily" in providers
    assert "serper" in providers
    assert "serpapi" in providers
    assert "google_programmable" in providers
    assert "duckduckgo" in providers
    assert "custom" in providers


def test_get_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown search provider"):
        get_search_provider("nonexistent")


def test_get_duckduckgo_provider():
    provider = get_search_provider("duckduckgo")
    assert provider.name == "duckduckgo"
    assert provider.requires_api_key is False


def test_get_brave_provider():
    provider = get_search_provider("brave")
    assert provider.name == "brave"
    assert provider.requires_api_key is True


def test_provider_status_returns_list():
    statuses = get_provider_status()
    assert isinstance(statuses, list)
    assert len(statuses) >= 7
    names = [s["name"] for s in statuses]
    assert "duckduckgo" in names


def test_duckduckgo_validates_without_key():
    provider = get_search_provider("duckduckgo")
    ok, msg = provider.validate_config()
    assert ok is True


def test_brave_fails_without_key():
    provider = get_search_provider("brave")
    ok, msg = provider.validate_config()
    assert ok is False
    assert "BRAVE_SEARCH_API_KEY" in msg


def test_custom_fails_without_base_url():
    provider = get_search_provider("custom")
    ok, msg = provider.validate_config()
    assert ok is False
    assert "base_url" in msg


# ======================================================================
# Service
# ======================================================================

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

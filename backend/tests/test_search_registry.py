"""Tests for search provider registry."""

import pytest

from patchquest.search.search_registry import (
    get_search_provider,
    list_search_providers,
    get_provider_status,
)


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

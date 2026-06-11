"""Tests for search data models."""

from patchquest.search.search_models import ResultType, SearchOptions, SearchResponse, SearchResult


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

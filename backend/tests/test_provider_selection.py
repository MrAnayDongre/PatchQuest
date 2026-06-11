"""Tests for LLM provider selection end-to-end."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from patchquest.agents.provider_base import ModelConfig, ProviderResponse
from patchquest.agents.provider_registry import PROVIDERS, get_provider
from patchquest.agents.providers_groq import GroqProvider
from patchquest.agents.providers_mock import MockProvider
from patchquest.api.schemas import CreateRunRequest, RunResponse
from patchquest.config import AppConfig, set_config
from patchquest.database import init_db, set_db_path
from patchquest.orchestrator.run_context import RunContext
from patchquest.reports.final_report import generate_report


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    init_db()
    set_config(AppConfig())
    yield


# --- Provider registry ---

class TestProviderRegistry:
    def test_get_known_provider(self):
        p = get_provider("mock")
        assert isinstance(p, MockProvider)

    def test_get_groq_provider(self):
        p = get_provider("groq")
        assert isinstance(p, GroqProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_provider("nonexistent_provider_xyz")

    def test_no_silent_fallback_to_mock(self):
        with pytest.raises(ValueError):
            get_provider("does_not_exist")


# --- Groq provider validation ---

class TestGroqProvider:
    def test_validate_config_no_key(self):
        provider = GroqProvider()
        config = ModelConfig(provider="groq", model="llama-3.1-8b-instant")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GROQ_API_KEY", None)
            valid, err = provider.validate_config(config)
            assert not valid
            assert "GROQ_API_KEY" in err

    def test_validate_config_with_key(self):
        provider = GroqProvider()
        config = ModelConfig(provider="groq", model="llama-3.1-8b-instant",
                             api_key_env="GROQ_API_KEY")
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key-123"}):
            valid, err = provider.validate_config(config)
            assert valid
            assert err == ""


# --- CreateRunRequest schema ---

class TestCreateRunRequest:
    def test_defaults(self):
        req = CreateRunRequest(repo_path="/tmp/repo", task="fix bug")
        assert req.provider == "mock"
        assert req.model is None
        assert req.runtime_mode == "local"

    def test_custom_values(self):
        req = CreateRunRequest(
            repo_path="/tmp/repo", task="fix bug",
            provider="groq", model="llama-3.1-8b-instant", runtime_mode="docker",
        )
        assert req.provider == "groq"
        assert req.model == "llama-3.1-8b-instant"
        assert req.runtime_mode == "docker"


# --- RunResponse schema ---

class TestRunResponse:
    def test_has_provider_fields(self):
        resp = RunResponse(
            id="abc", repo_path="/tmp", task="t", status="created",
            provider="groq", model="llama-3.1-8b-instant", runtime_mode="local",
            created_at="now", updated_at="now",
        )
        assert resp.provider == "groq"
        assert resp.model == "llama-3.1-8b-instant"
        assert resp.runtime_mode == "local"


# --- RunContext carries provider info ---

class TestRunContext:
    def test_context_provider_fields(self):
        ctx = RunContext(
            run_id="r1", repo_path="/tmp", task="t",
            provider="groq", model="llama-3.1-8b-instant", runtime_mode="docker",
        )
        assert ctx.provider == "groq"
        assert ctx.model == "llama-3.1-8b-instant"
        assert ctx.runtime_mode == "docker"

    def test_context_defaults(self):
        ctx = RunContext(run_id="r1", repo_path="/tmp", task="t")
        assert ctx.provider == "mock"
        assert ctx.model is None
        assert ctx.runtime_mode == "local"


# --- Final report ---

class TestFinalReport:
    def test_mock_shows_limitation(self):
        ctx = RunContext(run_id="r1", repo_path="/tmp", task="t", provider="mock")
        report = generate_report(ctx)
        assert "mock provider" in report["report_md"].lower()
        assert "Limitations" in report["report_md"]

    def test_real_provider_no_limitation(self):
        ctx = RunContext(run_id="r1", repo_path="/tmp", task="t",
                         provider="groq", model="llama-3.1-8b-instant")
        report = generate_report(ctx)
        assert "Limitations" not in report["report_md"]
        assert "**Provider:** groq" in report["report_md"]
        assert "**Model:** llama-3.1-8b-instant" in report["report_md"]

    def test_report_includes_runtime(self):
        ctx = RunContext(run_id="r1", repo_path="/tmp", task="t",
                         provider="groq", model="m", runtime_mode="docker")
        report = generate_report(ctx)
        assert "**Runtime:** docker" in report["report_md"]


# --- DB migration ---

class TestDBMigration:
    def test_provider_columns_exist(self):
        from patchquest.database import get_db
        with get_db() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        assert "provider" in cols
        assert "model" in cols
        assert "runtime_mode" in cols

    def test_insert_and_read_provider_fields(self):
        from patchquest.database import get_db, now_iso
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO runs (id, repo_path, task, status, provider, model, runtime_mode,
                   created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("test1", "/tmp", "task", "created", "groq", "llama-3.1-8b-instant",
                 "docker", now, now),
            )
            row = conn.execute("SELECT * FROM runs WHERE id = 'test1'").fetchone()
        assert row["provider"] == "groq"
        assert row["model"] == "llama-3.1-8b-instant"
        assert row["runtime_mode"] == "docker"


# --- Provider routes ---

@pytest.mark.asyncio
async def test_list_providers():
    from patchquest.api.routes_providers import list_providers
    result = await list_providers()
    names = [p.name for p in result]
    assert "mock" in names
    assert "groq" in names
    assert "openai" in names


@pytest.mark.asyncio
async def test_provider_status():
    from patchquest.api.routes_providers import provider_status
    result = await provider_status()
    mock_st = next(s for s in result if s.name == "mock")
    assert mock_st.available is True


@pytest.mark.asyncio
async def test_test_provider_mock():
    from patchquest.api.routes_providers import test_provider
    from patchquest.api.schemas import ProviderTestRequest
    result = await test_provider(ProviderTestRequest(provider="mock"))
    assert result.available is True


@pytest.mark.asyncio
async def test_test_provider_unknown():
    from patchquest.api.routes_providers import test_provider
    from patchquest.api.schemas import ProviderTestRequest
    result = await test_provider(ProviderTestRequest(provider="nonexistent"))
    assert result.available is False
    assert "Unknown provider" in (result.error or "")


# --- Roles use run context provider ---

@pytest.mark.asyncio
async def test_call_role_uses_context_provider():
    from patchquest.agents.roles import _call_role

    mock_resp = ProviderResponse(
        content='{"result": "ok"}',
        usage={},
        model="llama-3.1-8b-instant",
    )
    ctx = RunContext(
        run_id="r1", repo_path="/tmp", task="t",
        provider="mock", model="mock-intake",
    )
    result = await _call_role("intake", "system prompt", "user content", ctx=ctx)
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_call_role_fails_for_invalid_provider():
    from patchquest.agents.roles import _call_role

    ctx = RunContext(
        run_id="r1", repo_path="/tmp", task="t",
        provider="nonexistent_xyz",
    )
    with pytest.raises((ValueError, RuntimeError)):
        await _call_role("intake", "sys", "user", ctx=ctx)

"""Tests for NVIDIA Responses API provider."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from patchquest.agents.provider_base import ModelConfig, ProviderResponse
from patchquest.agents.provider_registry import PROVIDERS, get_provider
from patchquest.agents.providers_nvidia import (
    NvidiaProvider,
    _extract_output_text,
    _messages_to_responses_input,
    _redact_key,
)
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

class TestNvidiaInRegistry:
    def test_nvidia_in_providers(self):
        assert "nvidia" in PROVIDERS

    def test_get_nvidia_provider(self):
        p = get_provider("nvidia")
        assert isinstance(p, NvidiaProvider)


# --- Provider catalog / API ---

@pytest.mark.asyncio
async def test_nvidia_in_provider_list():
    from patchquest.api.routes_providers import list_providers
    result = await list_providers()
    names = [p.name for p in result]
    assert "nvidia" in names
    nvidia = next(p for p in result if p.name == "nvidia")
    assert nvidia.display_name == "NVIDIA NIM / Build"
    assert nvidia.api_key_env == "NVIDIA_API_KEY"
    assert nvidia.default_model == "openai/gpt-oss-120b"
    assert "openai/gpt-oss-120b" in nvidia.models


@pytest.mark.asyncio
async def test_nvidia_status_unavailable_without_key():
    from patchquest.api.routes_providers import provider_status
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("NVIDIA_API_KEY", None)
        result = await provider_status()
    nvidia_st = next(s for s in result if s.name == "nvidia")
    assert nvidia_st.available is False
    assert nvidia_st.key_set is False


@pytest.mark.asyncio
async def test_nvidia_status_available_with_key():
    from patchquest.api.routes_providers import provider_status
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test-key-abc"}):
        result = await provider_status()
    nvidia_st = next(s for s in result if s.name == "nvidia")
    assert nvidia_st.available is True
    assert nvidia_st.key_set is True


@pytest.mark.asyncio
async def test_nvidia_status_never_exposes_raw_key():
    from patchquest.api.routes_providers import provider_status
    key = "nvapi-secret-key-12345"
    with patch.dict(os.environ, {"NVIDIA_API_KEY": key}):
        result = await provider_status()
    for st in result:
        assert key not in str(st.model_dump())


# --- Validation ---

class TestNvidiaValidation:
    def test_validate_missing_key(self):
        provider = NvidiaProvider()
        config = ModelConfig(provider="nvidia", model="openai/gpt-oss-120b")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NVIDIA_API_KEY", None)
            valid, err = provider.validate_config(config)
            assert not valid
            assert "NVIDIA_API_KEY" in err

    def test_validate_with_key(self):
        provider = NvidiaProvider()
        config = ModelConfig(provider="nvidia", model="openai/gpt-oss-120b",
                             api_key_env="NVIDIA_API_KEY")
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
            valid, err = provider.validate_config(config)
            assert valid
            assert err == ""


# --- Message conversion ---

class TestMessageConversion:
    def test_system_and_user(self):
        messages = [
            {"role": "system", "content": "You are a planner."},
            {"role": "user", "content": "Fix the bug."},
        ]
        result = _messages_to_responses_input(messages)
        assert "[System Instructions]" in result
        assert "You are a planner." in result
        assert "[User]" in result
        assert "Fix the bug." in result

    def test_preserves_all_messages(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user msg"},
            {"role": "assistant", "content": "prev response"},
            {"role": "user", "content": "follow up"},
        ]
        result = _messages_to_responses_input(messages)
        assert "[System Instructions]" in result
        assert "[User]" in result
        assert "[Assistant]" in result
        assert "follow up" in result

    def test_single_user_message(self):
        messages = [{"role": "user", "content": "hello"}]
        result = _messages_to_responses_input(messages)
        assert result == "[User]\nhello"

    def test_empty_messages(self):
        result = _messages_to_responses_input([])
        assert result == ""

    def test_uses_responses_endpoint_not_chat_completions(self):
        provider = NvidiaProvider()
        config = ModelConfig(
            provider="nvidia",
            model="openai/gpt-oss-120b",
            base_url="https://integrate.api.nvidia.com/v1",
        )
        url = f"{config.base_url.rstrip('/')}/responses"
        assert "/responses" in url
        assert "/chat/completions" not in url


# --- Output extraction ---

class TestOutputExtraction:
    def test_output_text_field(self):
        data = {"output_text": "Hello world"}
        assert _extract_output_text(data) == "Hello world"

    def test_message_content_items(self):
        data = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Response text here"}
                    ]
                }
            ]
        }
        assert _extract_output_text(data) == "Response text here"

    def test_text_items(self):
        data = {"output": [{"text": "Simple text"}]}
        assert _extract_output_text(data) == "Simple text"

    def test_string_items(self):
        data = {"output": ["raw string output"]}
        assert _extract_output_text(data) == "raw string output"

    def test_fallback_for_unknown_shape(self):
        data = {"output": [{"unknown_field": True}]}
        result = _extract_output_text(data)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_output(self):
        data = {"output": []}
        result = _extract_output_text(data)
        assert isinstance(result, str)

    def test_nested_content_text_field(self):
        data = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"text": "fallback text field"}
                    ]
                }
            ]
        }
        assert _extract_output_text(data) == "fallback text field"

    def test_reasoning_text_excluded(self):
        data = {
            "output": [
                {"type": "reasoning_text", "text": "internal reasoning"},
                {
                    "type": "message",
                    "content": [
                        {"type": "reasoning_text", "text": "more reasoning"},
                        {"type": "output_text", "text": "Final visible answer"},
                    ],
                },
            ]
        }
        result = _extract_output_text(data)
        assert result == "Final visible answer"
        assert "reasoning" not in result.lower()


# --- Key redaction ---

class TestKeyRedaction:
    def test_redacts_key_from_error(self):
        result = _redact_key("Error: auth failed with key nvapi-abc123", "nvapi-abc123")
        assert "nvapi-abc123" not in result
        assert "***REDACTED***" in result

    def test_no_key_no_redaction(self):
        result = _redact_key("Error: timeout", "")
        assert result == "Error: timeout"


# --- Retry behavior (unit-level) ---

class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_401_does_not_retry(self):
        """401 auth errors should fail immediately without retry."""
        import httpx

        provider = NvidiaProvider()
        config = ModelConfig(
            provider="nvidia", model="openai/gpt-oss-120b",
            api_key_env="NVIDIA_API_KEY",
            base_url="https://integrate.api.nvidia.com/v1",
        )
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = httpx.Response(
                401,
                text='{"error": "unauthorized"}',
                request=httpx.Request("POST", "https://test/responses"),
            )
            return resp

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = mock_post
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                with pytest.raises(RuntimeError, match="401"):
                    await provider.complete(
                        [{"role": "user", "content": "test"}], config
                    )

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_fallback_to_mock(self):
        """NVIDIA provider should never silently return mock results."""
        provider = NvidiaProvider()
        config = ModelConfig(
            provider="nvidia", model="openai/gpt-oss-120b",
            api_key_env="NVIDIA_API_KEY",
        )
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NVIDIA_API_KEY", None)
            with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
                await provider.complete(
                    [{"role": "user", "content": "test"}], config
                )


# --- Final report with NVIDIA ---

class TestFinalReportNvidia:
    def test_report_shows_nvidia_provider(self):
        ctx = RunContext(
            run_id="r1", repo_path="/tmp", task="t",
            provider="nvidia", model="openai/gpt-oss-120b",
        )
        report = generate_report(ctx)
        assert "**Provider:** nvidia" in report["report_md"]
        assert "**Model:** openai/gpt-oss-120b" in report["report_md"]
        assert "Limitations" not in report["report_md"]
        assert "mock" not in report["report_md"].lower()

    def test_report_shows_nvidia_runtime(self):
        ctx = RunContext(
            run_id="r1", repo_path="/tmp", task="t",
            provider="nvidia", model="openai/gpt-oss-120b",
            runtime_mode="docker",
        )
        report = generate_report(ctx)
        assert "**Runtime:** docker" in report["report_md"]


# --- Test provider test endpoint ---

@pytest.mark.asyncio
async def test_test_provider_nvidia_no_key():
    from patchquest.api.routes_providers import test_provider
    from patchquest.api.schemas import ProviderTestRequest
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("NVIDIA_API_KEY", None)
        result = await test_provider(ProviderTestRequest(provider="nvidia"))
    assert result.available is False
    assert "NVIDIA_API_KEY" in (result.error or "")


# --- Integration: roles with nvidia context ---

@pytest.mark.asyncio
async def test_call_role_nvidia_context_uses_responses_api():
    """Verify that when ctx.provider='nvidia', the role call goes through NvidiaProvider."""
    from patchquest.agents.roles import _call_role

    response_data = {
        "output_text": '{"task_type": "code_change", "target_languages": ["python"]}',
        "model": "openai/gpt-oss-120b",
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "status": "completed",
    }

    async def mock_complete(self, messages, config, response_format=None):
        return ProviderResponse(
            content=response_data["output_text"],
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            model="openai/gpt-oss-120b",
        )

    ctx = RunContext(
        run_id="r1", repo_path="/tmp", task="test",
        provider="nvidia", model="openai/gpt-oss-120b",
    )

    with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
        with patch.object(NvidiaProvider, "complete", mock_complete):
            result = await _call_role("intake", "system", "user", ctx=ctx)

    assert isinstance(result, dict)
    assert result.get("task_type") == "code_change"

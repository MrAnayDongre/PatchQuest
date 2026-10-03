"""Provider selection and the concrete adapters (Groq, NVIDIA Responses API, mock).

Invariants: the provider chosen for a run is the one roles call; invalid providers fail loudly; each adapter maps
its API's request/response shape and error cases onto the common contract.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from patchquest.agents.provider_base import ModelConfig, ProviderResponse
from patchquest.agents.provider_registry import PROVIDERS, get_provider
from patchquest.agents.providers_groq import GroqProvider
from patchquest.agents.providers_mock import MockProvider
from patchquest.agents.providers_nvidia import (
    NvidiaProvider,
    _extract_output_text,
    _messages_to_responses_input,
    _redact_key,
)
from patchquest.api.schemas import CreateRunRequest, RunResponse
from patchquest.orchestrator.run_context import RunContext
from patchquest.reports.final_report import generate_report

# ======================================================================
# Provider selection
# ======================================================================

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


# ======================================================================
# NVIDIA adapter
# ======================================================================

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

    @pytest.mark.asyncio
    async def test_uses_responses_endpoint_not_chat_completions(self, monkeypatch):
        """The adapter must talk to the Responses API: /responses with an 'input' string (regression: the
        previous version of this test only asserted on a string it built itself)."""
        import json

        import httpx

        seen = {}

        def handler(request):
            seen["url"], seen["body"] = str(request.url), json.loads(request.content)
            return httpx.Response(200, json={"output_text": "hi", "status": "completed",
                                             "usage": {"input_tokens": 1, "output_tokens": 1}})

        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
        resp = await NvidiaProvider().complete(
            [{"role": "user", "content": "hello"}],
            ModelConfig(provider="nvidia", model="openai/gpt-oss-120b", base_url="https://integrate.api.nvidia.com/v1"),
        )
        assert seen["url"] == "https://integrate.api.nvidia.com/v1/responses"
        assert "input" in seen["body"] and "messages" not in seen["body"]
        assert resp.content == "hi"


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


def test_strip_reasoning_removes_think_blocks():
    from patchquest.agents.providers_openai_compatible import strip_reasoning

    assert strip_reasoning('<think>hmm</think>\n{"a": 1}') == '{"a": 1}'
    assert strip_reasoning('{"a": 1}') == '{"a": 1}'
    assert strip_reasoning("<think>ran out of budget") == ""


def test_sglang_and_vllm_disable_thinking_others_do_not():
    from patchquest.agents.providers_openai_compatible import LlamaCppProvider, SGLangProvider, VLLMProvider

    assert SGLangProvider.extra_body["chat_template_kwargs"] == {"enable_thinking": False}
    assert VLLMProvider.extra_body == SGLangProvider.extra_body and not LlamaCppProvider.extra_body

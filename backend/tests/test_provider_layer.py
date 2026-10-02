"""Provider contract: structured output, explicit degradation, retries, budgets, recording."""

import json

import httpx
import pytest

from patchquest.agents import providers_openai_compatible as poc
from patchquest.agents import roles
from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.provider_registry import get_provider
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.agents.structured_outputs import PatchOutput, PlannerOutput, coerce, json_schema_for
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db, init_db, set_db_path
from patchquest.orchestrator.run_context import RunContext

OK_BODY = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}], "model": "m",
           "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
SCHEMA_RF = {"type": "json_schema", "json_schema": {"name": "X", "schema": {"type": "object"}, "strict": False}}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    set_db_path(tmp_path / "pl.db")
    init_db()
    set_config(AppConfig())
    poc._UNSUPPORTED.clear()
    monkeypatch.setattr(roles, "BACKOFF_SECONDS", (0, 0))
    yield
    poc._UNSUPPORTED.clear()
    set_config(AppConfig())


def fake_server(monkeypatch, handler):
    calls = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        return handler(request, calls)

    monkeypatch.setattr(poc, "client_factory",
                        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(wrapped), timeout=timeout))
    return calls


def cfg(**kw):
    return ModelConfig(provider="sglang", model="qwen", base_url="http://engine:30000/v1", **kw)


class TestOpenAICompatible:
    @pytest.mark.asyncio
    async def test_sends_json_schema_and_reports_usage_and_latency(self, monkeypatch):
        calls = fake_server(monkeypatch, lambda r, c: httpx.Response(200, json=OK_BODY))
        resp = await get_provider("sglang").complete([{"role": "user", "content": "hi"}], cfg(), SCHEMA_RF)
        assert calls[0]["url"] == "http://engine:30000/v1/chat/completions"
        assert calls[0]["body"]["response_format"]["type"] == "json_schema"
        assert resp.usage["prompt_tokens"] == 11 and resp.latency_s is not None
        assert resp.degraded == [] and resp.attempts == 1

    @pytest.mark.asyncio
    async def test_unsupported_feature_degrades_explicitly_and_is_remembered(self, monkeypatch):
        def handler(request, calls):
            if "response_format" in calls[-1]["body"]:
                return httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}})
            return httpx.Response(200, json=OK_BODY)

        calls = fake_server(monkeypatch, handler)
        provider = get_provider("sglang")
        first = await provider.complete([{"role": "user", "content": "x"}], cfg(), SCHEMA_RF)
        assert first.degraded == ["json_schema"] and first.attempts == 2 and len(calls) == 2
        second = await provider.complete([{"role": "user", "content": "x"}], cfg(), SCHEMA_RF)
        assert second.degraded == ["json_schema"] and len(calls) == 3  # no wasted rejected request
        assert "response_format" not in calls[-1]["body"]

    @pytest.mark.asyncio
    async def test_auth_errors_are_not_mistaken_for_missing_features(self, monkeypatch):
        fake_server(monkeypatch, lambda r, c: httpx.Response(401, json={"error": "invalid api key"}))
        with pytest.raises(httpx.HTTPStatusError):
            await get_provider("sglang").complete([{"role": "user", "content": "x"}], cfg(), SCHEMA_RF)
        assert not poc._UNSUPPORTED

    @pytest.mark.asyncio
    async def test_api_key_is_read_from_env_by_name(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-env-value")
        calls = fake_server(monkeypatch, lambda r, c: httpx.Response(200, json=OK_BODY))
        c = ModelConfig(provider="openai_compatible", model="m", base_url="http://x/v1", api_key_env="MY_KEY")
        await get_provider("openai_compatible").complete([{"role": "user", "content": "x"}], c)
        assert calls[0]["headers"]["authorization"] == "Bearer sk-env-value"

    def test_declared_capabilities(self):
        for name in ("vllm", "sglang", "llamacpp", "lmstudio", "ollama"):
            caps = get_provider(name).capabilities(ModelConfig(provider=name))
            assert caps.json_schema and caps.json_object, name
        assert not get_provider("openai_compatible").capabilities(ModelConfig()).json_schema
        assert get_provider("openai_compatible").capabilities(
            ModelConfig(capability_hints={"json_schema": True})).json_schema

    def test_provider_does_not_mutate_callers_config(self):
        c = ModelConfig(provider="groq", model="", api_key_env=None)
        import asyncio
        import os
        os.environ["GROQ_API_KEY"] = "x"
        try:
            with pytest.raises(Exception):  # noqa: B017 - network is not reachable in tests
                asyncio.run(get_provider("groq").complete([{"role": "user", "content": "x"}], c))
        finally:
            os.environ.pop("GROQ_API_KEY")
        assert c.api_key_env is None and c.model == ""


class TestSchemas:
    def test_schema_is_closed_for_constrained_decoding(self):
        schema = json_schema_for(PatchOutput)
        assert schema["additionalProperties"] is False and set(schema["required"]) >= {"edits", "create", "delete"}
        assert schema["$defs"]["EditOut"]["additionalProperties"] is False

    def test_sloppy_but_harmless_output_is_normalised(self):
        out = coerce(PlannerOutput, {"plan": "p", "tests_likely_needed": True, "files_to_inspect": "a.py", "test_commands": None})
        assert out["tests_likely_needed"] == [] and out["files_to_inspect"] == ["a.py"] and out["test_commands"] == []

    def test_response_format_selection_follows_capabilities(self):
        assert roles._response_format(get_provider("sglang"), ModelConfig(), PatchOutput)["type"] == "json_schema"
        assert roles._response_format(get_provider("openai_compatible"), ModelConfig(), PatchOutput) is None
        only_obj = ModelConfig(capability_hints={"json_object": True})
        assert roles._response_format(get_provider("openai_compatible"), only_obj, PatchOutput) == {"type": "json_object"}
        assert roles._response_format(get_provider("sglang"), ModelConfig(), None) is None


def ctx(name="pl-run"):
    return RunContext(run_id=name, repo_path="/x", task="t", provider="scripted", model=name)


class TestRoleCalls:
    @pytest.mark.asyncio
    async def test_invalid_reply_gets_one_repair_call_quoting_the_error(self):
        bad = {"edits": [{"search": "x"}]}  # missing path
        good = {"edits": [{"path": "a.py", "search": "x", "replace": "y"}]}
        script = ScriptedProvider.register("fmt-1", {"coder": [bad, good]})
        out = await roles._call_role("coder", "You are a code editor", "do it", ctx("fmt-1"), PatchOutput)
        assert out["edits"][0]["path"] == "a.py"
        calls = script.calls_for("coder")
        assert len(calls) == 2
        assert "edits.0.path" in calls[1]["messages"][-1]["content"] and calls[1]["messages"][-2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_persistently_invalid_reply_becomes_parse_error(self):
        ScriptedProvider.register("fmt-2", {"coder": [{"edits": [{"search": "x"}]}] * 3})
        out = await roles._call_role("coder", "You are a code editor", "do it", ctx("fmt-2"), PatchOutput)
        assert out["parse_error"] and "path" in out["validation_error"]

    @pytest.mark.asyncio
    async def test_repair_attempts_are_configurable_and_bounded(self):
        cfg_ = AppConfig()
        cfg_.agent.format_repair_attempts = 0
        set_config(cfg_)
        script = ScriptedProvider.register("fmt-3", {"coder": [{"edits": [{"search": "x"}]}] * 3})
        out = await roles._call_role("coder", "You are a code editor", "do it", ctx("fmt-3"), PatchOutput)
        assert out["parse_error"] and len(script.calls_for("coder")) == 1

    @pytest.mark.asyncio
    async def test_budget_is_enforced(self):
        cfg_ = AppConfig()
        cfg_.agent.max_model_calls = 2
        set_config(cfg_)
        ScriptedProvider.register("bud", {"planner": [{"plan": "p"}] * 5})
        c = ctx("bud")
        await roles._call_role("planner", "You are a task planner", "x", c)
        await roles._call_role("planner", "You are a task planner", "x", c)
        with pytest.raises(roles.BudgetExceeded):
            await roles._call_role("planner", "You are a task planner", "x", c)

    @pytest.mark.asyncio
    async def test_token_budget_is_enforced(self):
        cfg_ = AppConfig()
        cfg_.agent.max_total_tokens = 5
        set_config(cfg_)
        ScriptedProvider.register("tok", {"planner": [{"plan": "p"}] * 3})
        c = ctx("tok")
        c.tokens_used = 6
        with pytest.raises(roles.BudgetExceeded, match="token"):
            await roles._call_role("planner", "You are a task planner", "x", c)

    @pytest.mark.asyncio
    async def test_transient_errors_are_retried_then_succeed(self, monkeypatch):
        def handler(request, calls):
            return httpx.Response(429, json={"error": "slow down"}) if len(calls) < 3 else httpx.Response(200, json=OK_BODY)

        calls = fake_server(monkeypatch, handler)
        c = RunContext(run_id="retry", repo_path="/x", task="t", provider="sglang", model="qwen")
        assert await roles._call_role("intake", "x", "y", c) == {"ok": True}
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_permanent_errors_are_not_retried_and_keys_are_redacted(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecretvalue1234567890")
        calls = fake_server(monkeypatch, lambda r, c: httpx.Response(401, json={"error": "bad key sk-supersecretvalue1234567890"}))
        c = RunContext(run_id="perm", repo_path="/x", task="t", provider="openai", model="gpt")
        with pytest.raises(RuntimeError) as err:
            await roles._call_role("intake", "x", "y", c)
        assert len(calls) == 1 and "supersecret" not in str(err.value)


class TestRecording:
    @pytest.mark.asyncio
    async def test_calls_are_recorded_with_usage_and_redacted_io(self, monkeypatch):
        calls = fake_server(monkeypatch, lambda r, c: httpx.Response(200, json=OK_BODY))
        events = []

        async def sink(kind, payload):
            events.append((kind, payload))

        c = RunContext(run_id="rec", repo_path="/x", task="t", provider="sglang", model="qwen", event_sink=sink)
        await roles._call_role("intake", "sys", "token sk-abc123def456ghi789jkl012mno345pqr678", c)
        with get_db() as conn:
            row = conn.execute("SELECT * FROM model_calls WHERE run_id = 'rec'").fetchone()
        assert row["role"] == "intake" and row["provider"] == "sglang" and row["status"] == "ok"
        assert row["prompt_tokens"] == 11 and row["completion_tokens"] == 7 and row["duration_ms"] is not None
        assert "sk-abc123" not in row["request_json"] and "ok" in row["response_text"]
        assert c.model_calls == 1 and c.tokens_used == 18 and calls
        assert events and events[0][0] == "model_call" and events[0][1]["usage"]["prompt_tokens"] == 11

    @pytest.mark.asyncio
    async def test_io_recording_can_be_disabled(self, monkeypatch):
        fake_server(monkeypatch, lambda r, c: httpx.Response(200, json=OK_BODY))
        cfg_ = AppConfig()
        cfg_.agent.record_model_io = False
        set_config(cfg_)
        c = RunContext(run_id="noio", repo_path="/x", task="t", provider="sglang", model="qwen")
        await roles._call_role("intake", "sys", "secret prompt", c)
        with get_db() as conn:
            row = conn.execute("SELECT * FROM model_calls WHERE run_id = 'noio'").fetchone()
        assert row["request_json"] is None and row["response_text"] is None and row["status"] == "ok"

    @pytest.mark.asyncio
    async def test_failures_are_recorded_too(self, monkeypatch):
        fake_server(monkeypatch, lambda r, c: httpx.Response(401, json={"error": "nope"}))
        c = RunContext(run_id="fail", repo_path="/x", task="t", provider="sglang", model="qwen")
        with pytest.raises(RuntimeError):
            await roles._call_role("intake", "sys", "x", c)
        with get_db() as conn:
            row = conn.execute("SELECT status, error FROM model_calls WHERE run_id = 'fail'").fetchone()
        assert row["status"] == "error" and "401" in row["error"]


class TestProbe:
    @pytest.mark.asyncio
    async def test_reports_models_latency_and_context_length(self):
        from patchquest.providers.probe import probe_endpoint

        def handler(request):
            assert str(request.url) == "http://engine/v1/models"
            return httpx.Response(200, json={"data": [{"id": "qwen3-0.6b", "max_model_len": 4096}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        r = await probe_endpoint("http://engine/v1/", client=client)
        assert r["ok"] and r["models"] == ["qwen3-0.6b"] and r["context_length"] == 4096 and r["latency_ms"] is not None

    @pytest.mark.asyncio
    async def test_down_and_error_endpoints_are_reported_not_raised(self):
        from patchquest.providers.probe import probe_endpoint

        def boom(request):
            raise httpx.ConnectError("refused")

        r = await probe_endpoint("http://nope/v1", client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
        assert not r["ok"] and "ConnectError" in r["error"]
        r = await probe_endpoint("http://x/v1", client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(503))))
        assert not r["ok"] and r["error"] == "HTTP 503"

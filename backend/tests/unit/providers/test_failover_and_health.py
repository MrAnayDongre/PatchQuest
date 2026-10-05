"""Failover only when it is safe and useful; health reflects what actually happened."""

import httpx
import pytest

from patchquest.agents import roles
from patchquest.agents.provider_base import Capabilities, ModelConfig
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.config import AgentConfig, AppConfig, FailoverConfig, FailoverTarget, set_config
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.orchestrator.run_context import RunContext
from patchquest.providers import health
from patchquest.providers.failover import check, is_local

CLOUD = Capabilities(json_schema=True)
LOCAL_JSON = Capabilities(json_schema=True)
NO_SCHEMA = Capabilities()


def mc(provider, base_url=None):
    return ModelConfig(provider=provider, model="m", base_url=base_url)


class TestPolicy:
    @pytest.mark.parametrize("provider,url,local", [
        ("sglang", "http://127.0.0.1:30000/v1", True), ("ollama", None, True), ("scripted", None, True),
        ("vllm", "http://localhost:8000/v1", True), ("sglang", "http://10.0.0.5:30000/v1", False),
        ("sglang", "https://models.example.com/v1", False), ("openai", None, False), ("anthropic", None, False)])
    def test_what_counts_as_on_this_machine(self, provider, url, local):
        assert is_local(mc(provider, url)) is local

    @pytest.mark.parametrize("kind", [FailureKind.MODEL_UNAVAILABLE, FailureKind.MODEL_TIMEOUT, FailureKind.MODEL_RATE_LIMIT])
    def test_provider_outages_may_fail_over(self, kind):
        assert check(kind, mc("openai"), CLOUD, mc("groq"), CLOUD, FailoverConfig(), constrained_output_used=True).allowed

    @pytest.mark.parametrize("kind", [FailureKind.MODEL_AUTH, FailureKind.MODEL_INVALID_OUTPUT, FailureKind.MODEL_CONTEXT_OVERFLOW,
                                      FailureKind.BUDGET_EXHAUSTED, FailureKind.INTERNAL_INVARIANT, FailureKind.PATCH_APPLY])
    def test_anything_else_never_fails_over(self, kind):
        verdict = check(kind, mc("openai"), CLOUD, mc("groq"), CLOUD, FailoverConfig(), constrained_output_used=False)
        assert not verdict.allowed and "not a provider outage" in verdict.reason

    def test_local_to_cloud_needs_explicit_permission(self):
        args = (FailureKind.MODEL_UNAVAILABLE, mc("sglang", "http://127.0.0.1:30000/v1"), LOCAL_JSON, mc("openai"), CLOUD)
        refused = check(*args, FailoverConfig(), constrained_output_used=False)
        assert not refused.allowed and "not on this machine" in refused.reason
        assert check(*args, FailoverConfig(allow_cloud=True), constrained_output_used=False).allowed

    def test_cloud_to_local_is_always_fine_privacy_wise(self):
        assert check(FailureKind.MODEL_TIMEOUT, mc("openai"), CLOUD, mc("ollama"), NO_SCHEMA, FailoverConfig(),
                     constrained_output_used=False).allowed

    def test_losing_constrained_output_is_refused_only_when_the_run_uses_it(self):
        args = (FailureKind.MODEL_UNAVAILABLE, mc("openai"), CLOUD, mc("ollama"), NO_SCHEMA)
        assert not check(*args, FailoverConfig(), constrained_output_used=True).allowed
        assert check(*args, FailoverConfig(), constrained_output_used=False).allowed
        assert check(*args, FailoverConfig(allow_capability_downgrade=True), constrained_output_used=True).allowed


def boom(_messages):
    raise httpx.ConnectError("refused")


def ctx_for(model, events):
    async def sink(kind, payload):
        events.append((kind, payload))

    return RunContext(run_id="fo", repo_path="/x", task="t", provider="scripted", model=model, event_sink=sink)


def configure(*targets, **kw):
    cfg = AppConfig(agent=AgentConfig(failover=FailoverConfig(chain=list(targets), **kw)))
    set_config(cfg)


PLAN = {"plan": "p", "files_to_inspect": [], "tests_likely_needed": [], "expected_patch_scope": "s",
        "stop_conditions": [], "test_commands": []}


class TestThroughTheModelCallPath:
    @pytest.mark.asyncio
    async def test_outage_on_the_primary_falls_over_to_the_next_provider(self):
        ScriptedProvider.register("primary", {"planner": [boom, boom, boom]})
        ScriptedProvider.register("backup", {"planner": [PLAN]})
        configure(FailoverTarget(provider="scripted", model="backup"))
        events = []
        out = await roles._call_role("planner", "You are a task planner", "x", ctx_for("primary", events))
        assert out["plan"] == "p"
        failover_events = [p for k, p in events if k == "provider_failover"]
        assert len(failover_events) == 1 and failover_events[0]["to"] == "scripted/backup"
        assert failover_events[0]["failure"]["kind"] == "MODEL_UNAVAILABLE"
        snapshot = {(h["model"]): h for h in health.snapshot()}
        assert snapshot["primary"]["status"] == "degraded" and snapshot["primary"]["last_error"] == "MODEL_UNAVAILABLE"
        assert snapshot["backup"]["status"] == "healthy" and snapshot["backup"]["successes"] == 1

    @pytest.mark.asyncio
    async def test_retries_are_spent_on_the_primary_before_failing_over(self):
        calls = {"n": 0}

        def counting_boom(_m):
            calls["n"] += 1
            raise httpx.ConnectError("refused")

        ScriptedProvider.register("primary", {"planner": [counting_boom] * 5})
        ScriptedProvider.register("backup", {"planner": [PLAN]})
        configure(FailoverTarget(provider="scripted", model="backup"))
        await roles._call_role("planner", "You are a task planner", "x", ctx_for("primary", []))
        assert calls["n"] == 3  # the policy's attempt cap, then the switch

    @pytest.mark.asyncio
    async def test_a_cloud_fallback_for_a_local_run_is_refused_and_the_original_error_stands(self):
        ScriptedProvider.register("primary", {"planner": [boom] * 3})
        configure(FailoverTarget(provider="openai", model="gpt-4o-mini"))
        events = []
        with pytest.raises(PatchQuestError) as err:
            await roles._call_role("planner", "You are a task planner", "x", ctx_for("primary", events))
        assert err.value.kind is FailureKind.MODEL_UNAVAILABLE
        refused = [p for k, p in events if k == "provider_failover_refused"]
        assert len(refused) == 1 and "not on this machine" in refused[0]["reason"]
        assert not any(k == "provider_failover" for k, _ in events)

    @pytest.mark.asyncio
    async def test_cloud_fallback_when_explicitly_allowed_is_attempted(self, monkeypatch):
        ScriptedProvider.register("primary", {"planner": [boom] * 3})
        configure(FailoverTarget(provider="openai", model="gpt-4o-mini"), allow_cloud=True)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        events = []
        with pytest.raises(PatchQuestError):  # the cloud target then fails for want of a key, which is its own error
            await roles._call_role("planner", "You are a task planner", "x", ctx_for("primary", events))
        assert [k for k, _ in events].count("provider_failover") == 1

    @pytest.mark.asyncio
    async def test_non_outage_failures_do_not_fail_over(self):
        req = httpx.Request("POST", "http://x")

        def unauthorized(_m):
            raise httpx.HTTPStatusError("e", request=req, response=httpx.Response(401, request=req))

        ScriptedProvider.register("primary", {"planner": [unauthorized]})
        ScriptedProvider.register("backup", {"planner": [PLAN]})
        configure(FailoverTarget(provider="scripted", model="backup"))
        events = []
        with pytest.raises(PatchQuestError) as err:
            await roles._call_role("planner", "You are a task planner", "x", ctx_for("primary", events))
        assert err.value.kind is FailureKind.MODEL_AUTH
        assert [k for k, _ in events if k.startswith("provider_failover")] == ["provider_failover_refused"]

    @pytest.mark.asyncio
    async def test_no_chain_means_no_failover_and_no_extra_events(self):
        ScriptedProvider.register("primary", {"planner": [boom] * 3})
        events = []
        with pytest.raises(PatchQuestError):
            await roles._call_role("planner", "You are a task planner", "x", ctx_for("primary", events))
        assert not [k for k, _ in events if k.startswith("provider_failover")]

    @pytest.mark.asyncio
    async def test_failover_is_one_model_call_against_the_budget(self):
        ScriptedProvider.register("primary", {"planner": [boom] * 3})
        ScriptedProvider.register("backup", {"planner": [PLAN]})
        configure(FailoverTarget(provider="scripted", model="backup"))
        c = ctx_for("primary", [])
        await roles._call_role("planner", "You are a task planner", "x", c)
        assert c.model_calls == 1


class TestHealth:
    def test_status_progression(self):
        config = mc("sglang", "http://127.0.0.1:1/v1")
        health.record_success(config, 40)
        assert health.snapshot()[0]["status"] == "healthy" and health.snapshot()[0]["last_latency_ms"] == 40
        health.record_failure(config, FailureKind.MODEL_TIMEOUT)
        assert health.snapshot()[0]["status"] == "degraded"
        health.record_failure(config, FailureKind.MODEL_TIMEOUT)
        health.record_failure(config, FailureKind.MODEL_TIMEOUT)
        assert health.snapshot()[0]["status"] == "down"
        health.record_success(config, 12)
        assert health.snapshot()[0]["status"] == "healthy"  # one success clears the streak

    def test_endpoints_are_tracked_separately(self):
        health.record_success(mc("sglang", "http://127.0.0.1:1/v1"), 1)
        health.record_failure(mc("sglang", "http://127.0.0.1:2/v1"), FailureKind.MODEL_UNAVAILABLE)
        assert {h["base_url"]: h["status"] for h in health.snapshot()} == {
            "http://127.0.0.1:1/v1": "healthy", "http://127.0.0.1:2/v1": "degraded"}


class TestEngineReport:
    @pytest.mark.asyncio
    async def test_report_merges_probe_results_with_observed_health(self, monkeypatch):
        from patchquest.providers import engines

        async def fake_probe(url, key_env=None, **_):
            if ":30000" in url:  # sglang
                return {"ok": True, "latency_ms": 7, "models": ["qwen"], "context_length": 4096}
            return {"ok": False, "error": "ConnectError: refused", "models": []}

        monkeypatch.setattr(engines, "probe_endpoint", fake_probe)
        health.record_failure(mc("sglang", "http://localhost:30000/v1"), FailureKind.MODEL_TIMEOUT)
        rows = {r["engine"]: r for r in await engines.engine_report()}
        assert rows["sglang"]["available"] and rows["sglang"]["model_loaded"] and rows["sglang"]["context_limit"] == 4096
        assert rows["sglang"]["healthy"] is False and rows["sglang"]["last_error"] == "MODEL_TIMEOUT"  # reachable, but failing in use
        assert rows["sglang"]["capabilities"]["json_schema"] is True and rows["sglang"]["latency_ms"] == 7
        assert rows["vllm"]["available"] is False and "refused" in rows["vllm"]["last_error"]
        assert set(rows) == {"sglang", "vllm", "llamacpp", "lmstudio", "ollama"}

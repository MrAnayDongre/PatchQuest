from patchquest.agents import budget
from patchquest.agents.budget import effective_max_tokens, fit_text, is_context_overflow, prompt_budget_chars
from patchquest.agents.provider_base import ModelConfig


def _cfg(**kw):
    return ModelConfig(provider="openai_compatible", model="m", **kw)


def test_small_text_is_untouched():
    assert fit_text("hello", 1000) == "hello"


def test_blocks_shrink_instructions_survive():
    text = "Task: fix it\n<file path=\"a.py\">\n" + "x" * 5000 + "\n</file>\nEnd"
    out = fit_text(text, 1000)
    assert len(out) < 1500 and out.startswith("Task: fix it") and out.endswith("End")
    assert "truncated to fit" in out


def test_blocks_share_budget_proportionally():
    big, small = "b" * 6000, "s" * 600
    out = fit_text(f"<file path=\"b\">{big}</file><file path=\"s\">{small}</file>", 2000)
    assert out.count("s") < 700 and 600 < out.count("b") < 2000


def test_no_blocks_cuts_middle():
    out = fit_text("a" * 3000 + "z" * 3000, 1000)
    assert out.startswith("a") and out.endswith("z") and len(out) <= 1000


def test_overflow_detection():
    assert is_context_overflow(400, "maximum context length is 4096")
    assert not is_context_overflow(500, "maximum context length")
    assert not is_context_overflow(400, "invalid api key")


def test_effective_max_tokens_reserves_room():
    assert effective_max_tokens(_cfg(max_tokens=4096), 4096) == 1024
    assert effective_max_tokens(_cfg(max_tokens=4096), None) == 4096
    assert effective_max_tokens(_cfg(max_tokens=512), 100000) == 512


def test_prompt_budget_shrinks_with_retry_factor():
    assert prompt_budget_chars(4096, 1024, "sys", 0.5) < prompt_budget_chars(4096, 1024, "sys")


async def test_context_limit_prefers_hint_then_probes_once(monkeypatch):
    assert await budget.context_limit(_cfg(capability_hints={"max_context": 8192})) == 8192
    calls = []

    async def fake_probe(url, env, timeout):
        calls.append(url)
        return {"ok": True, "context_by_model": {"m": 2048}}

    monkeypatch.setattr(budget, "probe_endpoint", fake_probe)
    monkeypatch.setattr(budget, "_LIMITS", {})
    cfg = _cfg(base_url="http://x/v1")
    assert await budget.context_limit(cfg) == 2048 and await budget.context_limit(cfg) == 2048
    assert len(calls) == 1

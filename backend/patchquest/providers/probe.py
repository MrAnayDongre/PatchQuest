"""Probe an OpenAI-compatible endpoint: is it up, how fast, which models, what context length."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from patchquest.providers.catalog import PROVIDER_CATALOG

LOCAL_ENGINES = ("sglang", "vllm", "llamacpp", "lmstudio", "ollama")


def _context_length(model: dict[str, Any]) -> int | None:
    # vLLM and SGLang report max_model_len; llama.cpp/LM Studio/Ollama use other names or omit it.
    for key in ("max_model_len", "context_length", "n_ctx", "max_context_length"):
        value = model.get(key) or (model.get("meta") or {}).get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


async def probe_endpoint(base_url: str, api_key_env: str | None = None, timeout: float = 3.0,  # noqa: ASYNC109 - httpx client timeout, not a cancellation scope
                         client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/models"
    headers = {}
    key = os.environ.get(api_key_env, "") if api_key_env else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    started = time.monotonic()
    try:
        resp = await client.get(url, headers=headers)
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            return {"ok": False, "url": url, "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}", "models": []}
        data = resp.json().get("data") or []
        by_model = {m["id"]: c for m in data if m.get("id") and (c := _context_length(m))}
        return {
            "ok": True, "url": url, "latency_ms": latency_ms,
            "models": [m.get("id") for m in data if m.get("id")],
            "context_length": next(iter(by_model.values()), None),
            "context_by_model": by_model,
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "url": url, "latency_ms": None, "error": f"{type(exc).__name__}: {exc}", "models": []}
    finally:
        if own:
            await client.aclose()


async def probe_local_engines() -> dict[str, dict[str, Any]]:
    """Probe the default endpoint of every local serving engine in the catalogue."""
    out: dict[str, dict[str, Any]] = {}
    for entry in PROVIDER_CATALOG:
        if entry["name"] in LOCAL_ENGINES and entry.get("base_url"):
            out[entry["name"]] = await probe_endpoint(entry["base_url"], entry.get("api_key_env"))
    return out

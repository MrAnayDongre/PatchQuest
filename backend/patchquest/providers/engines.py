"""One report on the local serving engines: reachable, healthy, what is loaded, how big a context, what it can do."""

from __future__ import annotations

from typing import Any

from patchquest.providers import health
from patchquest.providers.catalog import PROVIDER_CATALOG
from patchquest.providers.probe import LOCAL_ENGINES, probe_endpoint


async def engine_report() -> list[dict[str, Any]]:
    """Probe each local engine at its default address and merge what this process has observed in use."""
    observed = health.snapshot()
    rows: list[dict[str, Any]] = []
    for entry in PROVIDER_CATALOG:
        if entry["name"] not in LOCAL_ENGINES or not entry.get("base_url"):
            continue
        probe = await probe_endpoint(entry["base_url"], entry.get("api_key_env"))
        models = probe.get("models") or []
        mine = [h for h in observed if h["provider"] == entry["name"]]
        rows.append({
            "engine": entry["name"],
            "url": entry["base_url"],
            "available": bool(probe["ok"]),
            "healthy": probe["ok"] and not any(h["status"] in ("down", "degraded") for h in mine),
            "models": models,
            "model_loaded": bool(models),
            "context_limit": probe.get("context_length"),
            "capabilities": entry.get("capabilities", {}),
            "latency_ms": probe.get("latency_ms"),
            "last_error": probe.get("error") or next((h["last_error"] for h in mine if h["last_error"]), None),
        })
    return rows

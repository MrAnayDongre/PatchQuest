"""Ollama provider (OpenAI-compatible endpoint)."""

from __future__ import annotations

from patchquest.agents.providers_openai_compatible import LocalEngineProvider


class OllamaProvider(LocalEngineProvider):
    engine, default_base_url = "ollama", "http://localhost:11434/v1"

"""Provider registry - maps provider names to implementations."""

from __future__ import annotations

from patchquest.agents.provider_base import ProviderBase
from patchquest.agents.providers_anthropic import AnthropicProvider
from patchquest.agents.providers_groq import GroqProvider
from patchquest.agents.providers_mock import MockProvider
from patchquest.agents.providers_nvidia import NvidiaProvider
from patchquest.agents.providers_ollama import OllamaProvider
from patchquest.agents.providers_openai import OpenAIProvider
from patchquest.agents.providers_openai_compatible import OpenAICompatibleProvider
from patchquest.agents.providers_openrouter import OpenRouterProvider

PROVIDERS: dict[str, type[ProviderBase]] = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
    "groq": GroqProvider,
    "nvidia": NvidiaProvider,
    "openrouter": OpenRouterProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


def get_provider(name: str) -> ProviderBase:
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider '{name}'. "
            f"Available providers: {', '.join(PROVIDERS.keys())}"
        )
    return cls()

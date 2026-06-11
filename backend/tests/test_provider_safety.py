"""Tests for provider configuration safety."""

import os

from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.provider_registry import PROVIDERS, get_provider
from patchquest.config import AppConfig, ModelsConfig, ModelProfile


def test_no_hardcoded_api_keys_in_config():
    config = AppConfig()
    for field_name in ("intake", "planner", "coder", "reviewer", "security", "memory_curator"):
        profile: ModelProfile = getattr(config.models, field_name)
        assert profile.api_key_env is None or profile.api_key_env.startswith("MOCK") or profile.api_key_env == ""


def test_provider_registry_has_all_providers():
    expected = {"mock", "openai", "anthropic", "ollama", "groq", "nvidia", "openrouter", "openai_compatible"}
    assert set(PROVIDERS.keys()) == expected


def test_unknown_provider_raises_error():
    import pytest
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_provider("nonexistent_provider")


def test_model_config_stores_env_var_name_not_value():
    config = ModelConfig(
        provider="openai",
        model="gpt-4",
        api_key_env="OPENAI_API_KEY",
    )
    assert "sk-" not in str(config)
    assert config.api_key_env == "OPENAI_API_KEY"


def test_mock_provider_returns_valid_json():
    import asyncio
    from patchquest.agents.providers_mock import MockProvider

    provider = MockProvider()
    config = ModelConfig(provider="mock", model="mock-intake")
    messages = [{"role": "user", "content": "test"}]

    response = asyncio.run(provider.complete(messages, config))
    assert response.content
    assert response.finish_reason == "stop"

    import json
    parsed = json.loads(response.content)
    assert isinstance(parsed, dict)


def test_mock_provider_never_returns_secrets():
    import asyncio
    import json
    from patchquest.agents.providers_mock import MockProvider
    from patchquest.tools.secret_guard import has_secrets

    provider = MockProvider()
    roles = ["mock-intake", "mock-planner", "mock-coder", "mock-reviewer", "mock-security", "mock-memory"]

    for role in roles:
        config = ModelConfig(provider="mock", model=role)
        response = asyncio.run(provider.complete([{"role": "user", "content": "test"}], config))
        assert not has_secrets(response.content), f"Mock provider returned secrets for {role}"

"""Static catalogue of supported model providers (suggested models, not a guarantee).

Local serving engines expose an OpenAI-compatible API. Their ``capabilities`` below are what
the engine documents; PatchQuest still degrades explicitly at runtime if a request using a
capability is rejected (see ``OpenAICompatibleProvider``), and ``patchquest providers --probe``
reports what a running endpoint actually serves.
"""

from __future__ import annotations

from typing import Any

PROVIDER_CATALOG: list[dict[str, Any]] = [
    {
        "name": "mock",
        "display_name": "Mock (Demo)",
        "api_key_env": None,
        "base_url": None,
        "default_model": "mock-default",
        "models": ["mock-default"],
    },
    {
        "name": "groq",
        "display_name": "Groq",
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.1-8b-instant",
        "models": [
            "llama-3.1-8b-instant",
            "llama-3.1-70b-versatile",
            "llama3-8b-8192",
            "llama3-70b-8192",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
    },
    {
        "name": "openai",
        "display_name": "OpenAI",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    },
    {
        "name": "anthropic",
        "display_name": "Anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-haiku-4-5-20251001",
        "models": ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"],
    },
    {
        "name": "ollama",
        "display_name": "Ollama (Local)",
        "api_key_env": None,
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "models": ["llama3", "codellama", "mistral", "phi3"],
    },
    {
        "name": "nvidia",
        "display_name": "NVIDIA NIM / Build",
        "api_key_env": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "openai/gpt-oss-120b",
        "models": [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        ],
    },
    {
        "name": "openrouter",
        "display_name": "OpenRouter",
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3-8b-instruct",
        "models": ["meta-llama/llama-3-8b-instruct", "mistralai/mixtral-8x7b-instruct"],
    },
    {
        "name": "vllm",
        "display_name": "vLLM (Local)",
        "api_key_env": None,
        "base_url": "http://localhost:8000/v1",
        "default_model": "",
        "models": [],
        "capabilities": {"json_schema": True, "json_object": True},
        "timeout_seconds": 300,
    },
    {
        "name": "sglang",
        "display_name": "SGLang (Local)",
        "api_key_env": None,
        "base_url": "http://localhost:30000/v1",
        "default_model": "",
        "models": [],
        "capabilities": {"json_schema": True, "json_object": True},
        "timeout_seconds": 300,
    },
    {
        "name": "llamacpp",
        "display_name": "llama.cpp server (Local)",
        "api_key_env": None,
        "base_url": "http://localhost:8080/v1",
        "default_model": "",
        "models": [],
        "capabilities": {"json_schema": True, "json_object": True},
        "timeout_seconds": 300,
    },
    {
        "name": "lmstudio",
        "display_name": "LM Studio (Local)",
        "api_key_env": None,
        "base_url": "http://localhost:1234/v1",
        "default_model": "",
        "models": [],
        "capabilities": {"json_schema": True, "json_object": True},
        "timeout_seconds": 300,
    },
    {
        "name": "openai_compatible",
        "display_name": "OpenAI-Compatible",
        "api_key_env": None,
        "base_url": None,
        "default_model": "custom",
        "models": [],
    },
]

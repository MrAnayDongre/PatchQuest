"""Prompt budgeting: never send a model more than its context window can hold.

Local engines serve small windows (a 0.6B model at 4k, many 7B quantisations at 8k). Sending a prompt
that does not fit is not a "model failure": the endpoint rejects it. The budgeter discovers the window
(provider hints or a one-off ``/models`` probe), shrinks the *delimited data blocks* of the prompt
(``<file>`` and ``<output>``) proportionally, and keeps instructions and the task intact.
"""

from __future__ import annotations

import re

from patchquest.agents.provider_base import ModelConfig
from patchquest.providers.probe import probe_endpoint

# Conservative for code (it tokenises worse than prose); under-filling is cheaper than an HTTP 400.
CHARS_PER_TOKEN = 3.0
MIN_BLOCK_CHARS = 240
_BLOCK = re.compile(r"(<(?P<tag>file|output)\b[^>]*>)(?P<body>.*?)(</(?P=tag)>)", re.S)
_OVERFLOW_WORDS = ("context", "maximum", "too long", "exceed", "token limit", "max_model_len")

# (base_url, model) -> context window in tokens, or None when the endpoint does not say.
_LIMITS: dict[tuple[str, str], int | None] = {}


def is_context_overflow(status_code: int, body: str) -> bool:
    return status_code in (400, 413, 422) and any(w in body.lower() for w in _OVERFLOW_WORDS)


async def context_limit(config: ModelConfig) -> int | None:
    """Context window in tokens for this endpoint+model, if it can be determined."""
    hint = (config.capability_hints or {}).get("max_context")
    if isinstance(hint, int) and hint > 0:
        return hint
    if not config.base_url:
        return None
    key = (config.base_url, config.model)
    if key not in _LIMITS:
        info = await probe_endpoint(config.base_url, config.api_key_env, timeout=3.0)
        by_model = info.get("context_by_model") or {}
        _LIMITS[key] = by_model.get(config.model) or (info.get("context_length") if info.get("ok") else None)
    return _LIMITS[key]


def effective_max_tokens(config: ModelConfig, limit: int | None) -> int:
    """Leave room for the prompt: never ask for more than a quarter of a small window as output."""
    return config.max_tokens if not limit else min(config.max_tokens, max(256, limit // 4))


def _shrink(body: str, keep: int) -> str:
    if len(body) <= keep:
        return body
    marker = "\n...[truncated to fit the model's context window]...\n"
    head = max(0, (keep - len(marker)) * 2 // 3)
    tail = max(0, keep - len(marker) - head)
    return body[:head] + marker + (body[-tail:] if tail else "")


def fit_text(text: str, budget_chars: int) -> str:
    """Return ``text`` shrunk to at most ~``budget_chars`` by trimming data blocks, largest share first."""
    if len(text) <= budget_chars:
        return text
    blocks = list(_BLOCK.finditer(text))
    fixed = len(text) - sum(len(m.group("body")) for m in blocks)
    available = budget_chars - fixed
    if blocks and available >= MIN_BLOCK_CHARS * len(blocks):
        sizes = [len(m.group("body")) for m in blocks]
        total = sum(sizes)
        out, last = [], 0
        for m, size in zip(blocks, sizes, strict=True):
            keep = max(MIN_BLOCK_CHARS, int(available * size / total))
            out.append(text[last:m.start("body")])
            out.append(_shrink(m.group("body"), keep))
            last = m.end("body")
        out.append(text[last:])
        return "".join(out)
    return _shrink(text, max(budget_chars, MIN_BLOCK_CHARS))  # no blocks to trim: cut the middle


def prompt_budget_chars(limit: int, max_tokens: int, system_prompt: str, shrink: float = 1.0) -> int:
    """Characters available for the user message given the window, the output reserve and the system prompt."""
    prompt_tokens = max(256, limit - max_tokens)
    return max(MIN_BLOCK_CHARS, int(prompt_tokens * CHARS_PER_TOKEN * 0.9 * shrink) - len(system_prompt))

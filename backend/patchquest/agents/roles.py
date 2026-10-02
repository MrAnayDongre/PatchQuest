"""Role execution - each role is an isolated model call controlled by the orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from patchquest.agents.prompts import (
    ANALYSIS_SYSTEM,
    CONTEXT_BUILDER_SYSTEM,  # noqa: F401  (kept for importers)
    INTAKE_SYSTEM,
    PATCH_SYSTEM,
    PLANNER_SYSTEM,
    REPAIR_SYSTEM,
    REVIEWER_SYSTEM,
    SECURITY_SYSTEM,  # noqa: F401
)
from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.provider_registry import get_provider
from patchquest.config import get_config
from patchquest.context import build_context, render_context
from patchquest.orchestrator.run_context import RunContext
from patchquest.tools.secret_guard import redact_secrets

logger = logging.getLogger(__name__)

# Transient failures are retried with exponential backoff; everything else fails immediately.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 1.5)
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _resolve_model(role_name: str, ctx: RunContext | None):
    """Return ``(provider, ModelConfig)`` for a role, honouring the run's provider choice."""
    run_provider = ctx.provider if ctx else None
    run_model = ctx.model if ctx else None

    if run_provider and run_provider != "mock":
        from patchquest.api.routes_providers import PROVIDER_CATALOG

        catalog = next((p for p in PROVIDER_CATALOG if p["name"] == run_provider), None)
        nvidia = run_provider == "nvidia"
        return get_provider(run_provider), ModelConfig(
            provider=run_provider,
            model=run_model or (catalog["default_model"] if catalog else ""),
            base_url=catalog.get("base_url") if catalog else None,
            api_key_env=catalog.get("api_key_env") if catalog else None,
            max_tokens=4096 if nvidia else 2048,
            temperature=1.0 if nvidia else 0.2,
            top_p=1.0 if nvidia else None,
        )

    config = get_config()
    profile = getattr(config.models, role_name, config.models.intake)
    return get_provider(profile.provider), ModelConfig(
        provider=profile.provider,
        model=profile.model,
        base_url=profile.base_url,
        api_key_env=profile.api_key_env,
        max_tokens=profile.max_tokens,
        temperature=profile.temperature,
    )


def _is_transient(exc: BaseException) -> bool:
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


async def _complete(role_name: str, system_prompt: str, user_content: str, ctx: RunContext | None) -> str:
    provider, model_config = _resolve_model(role_name, ctx)

    valid, err = provider.validate_config(model_config)
    if not valid:
        raise RuntimeError(f"Provider '{model_config.provider}' configuration error: {err}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    last: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = await provider.complete(messages, model_config)
            return response.content or ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised below with context
            last = exc
            if attempt + 1 < MAX_ATTEMPTS and _is_transient(exc):
                delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                logger.warning("role=%s transient provider error (%s); retry in %.1fs", role_name, exc, delay)
                await asyncio.sleep(delay)
                continue
            break

    assert last is not None
    message = str(last)
    if model_config.api_key_env:
        key_value = os.environ.get(model_config.api_key_env, "")
        if key_value:
            message = message.replace(key_value, "***REDACTED***")
    raise RuntimeError(f"LLM provider '{model_config.provider}' call failed: {redact_secrets(message)}") from last


async def _call_role(role_name: str, system_prompt: str, user_content: str, ctx: RunContext | None = None) -> dict[str, Any]:
    return _parse_json_response(await _complete(role_name, system_prompt, user_content, ctx))


async def _call_role_text(role_name: str, system_prompt: str, user_content: str, ctx: RunContext | None = None) -> str:
    """Call an LLM role and return raw text (no JSON parsing)."""
    return (await _complete(role_name, system_prompt, user_content, ctx)).strip()


def _parse_json_response(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end])
            except json.JSONDecodeError:
                pass
        return {"raw_response": content, "parse_error": True}


async def run_intake_role(ctx: RunContext) -> dict[str, Any]:
    user_content = f"Task: {ctx.task}\nRepo: {ctx.repo_path}"
    return await _call_role("intake", INTAKE_SYSTEM, user_content, ctx=ctx)


async def run_planner_role(ctx: RunContext) -> dict[str, Any]:
    from patchquest.memory.repo_map import get_repo_map
    repo_map = get_repo_map(ctx.repo_path)
    file_summary = "\n".join(f["file_path"] for f in repo_map["files"][:50])
    user_content = f"Task: {ctx.task}\nRepo files:\n{file_summary}"
    return await _call_role("planner", PLANNER_SYSTEM, user_content, ctx=ctx)


async def run_context_builder(ctx: RunContext) -> dict[str, Any]:
    """Deterministic context selection from real files (no model call; see patchquest.context)."""
    from patchquest.memory.repo_map import get_repo_map

    planned = list(ctx.selected_files or [])
    items = build_context(ctx.repo_path, ctx.task, get_repo_map(ctx.repo_path), planned)
    return {
        "selected_files": [i.path for i in items],
        "context": {i.path: i.content for i in items},
        "provenance": [i.provenance() for i in items],
    }


async def run_analysis_role(ctx: RunContext) -> str:
    from patchquest.memory.repo_map import get_repo_map

    repo_map = get_repo_map(ctx.repo_path)

    selected = ctx.selected_context if isinstance(ctx.selected_context, dict) else {}
    context_summary = ""
    for path, content in list(selected.items())[:8]:
        context_summary += f"\n--- {path} ---\n{str(content)[:3000]}\n"

    if not context_summary.strip():
        context_summary = "(No file contents selected — use repo file list only.)\n"

    allowed_paths = list(selected.keys()) if selected else [f["file_path"] for f in repo_map["files"][:80]]
    allowed_block = "\n".join(f"- {p}" for p in allowed_paths[:80])

    user_content = (
        f"Task: {ctx.task}\n"
        f"Provider: {ctx.provider}\n"
        f"Model: {ctx.model or 'default'}\n"
        f"Runtime: {ctx.runtime_mode}\n\n"
        f"ALLOWED FILE PATHS (cite ONLY these exact paths, never invent others):\n{allowed_block}\n\n"
        f"Selected context:{context_summary}\n"
        "Answer the task directly in markdown."
    )
    return await _call_role_text("analyst", ANALYSIS_SYSTEM, user_content, ctx=ctx)


async def run_patch_role(ctx: RunContext) -> dict[str, Any]:
    from patchquest.orchestrator.run_context import _has_mutation_intent

    if "readme" in ctx.task.lower() and _has_mutation_intent(ctx.task.lower()):
        exact_patch = _build_readme_sentence_patch(ctx.repo_path, ctx.task)
        if exact_patch is not None:
            return exact_patch

    selected = ctx.selected_context if isinstance(ctx.selected_context, dict) else {}
    files = "\n".join(
        f'<file path="{path}">\n{str(content)[:8000]}\n</file>' for path, content in list(selected.items())[:8]
    ) or "(no files selected)"
    user_content = f"Task: {ctx.task}\n\nRepository files:\n{files}"
    return await _call_role("coder", PATCH_SYSTEM, user_content, ctx=ctx)


async def run_repair_role(ctx: RunContext, failures: list[dict[str, Any]], workspace_path: str, attempt: int) -> dict[str, Any]:
    """Ask the coder to fix a change that failed validation, using fresh file contents."""
    from patchquest.memory.repo_map import get_repo_map

    def describe(f: dict[str, Any]) -> str:
        cls = f.get("classification") or {}
        attribution = ""
        if cls:
            attribution = (
                f"NEW failures (caused by the change): {cls.get('new') or 'none'}\n"
                f"Failures that ALSO occur without the change: {cls.get('preexisting') or 'none'}\n"
            )
        return (
            f"$ {f.get('command')}\n(exit {f.get('returncode')})\n{attribution}"
            f"{redact_secrets((f.get('stdout') or '')[-3000:])}\n{redact_secrets((f.get('stderr') or '')[-3000:])}"
        )

    failure_text = "\n\n".join(describe(f) for f in failures)
    items = build_context(
        workspace_path, ctx.task, get_repo_map(ctx.repo_path), list(ctx.applied_files or []),
        failure_text=failure_text, budget_tokens=5000,
    )
    user_content = (
        f"Task: {ctx.task}\nRepair attempt {attempt}.\n\n"
        f"Current diff:\n{(ctx.proposed_diff or '')[:6000]}\n\n"
        f"Failing commands:\n<output>\n{failure_text}\n</output>\n\n"
        f"Current repository files:\n{render_context(items)}"
    )
    return await _call_role("coder", REPAIR_SYSTEM, user_content, ctx=ctx)


def _build_readme_sentence_patch(repo_path: str, task: str) -> dict[str, Any] | None:
    """Deterministic edit for tasks that ask to add an exact quoted sentence to README.md."""
    from patchquest.orchestrator.run_context import extract_quoted_sentence

    if "exactly this sentence" not in task.lower() and not extract_quoted_sentence(task):
        return None

    readme_path = os.path.join(repo_path, "README.md")
    if not os.path.isfile(readme_path):
        return None
    sentence = extract_quoted_sentence(task)
    if not sentence:
        return None

    with open(readme_path) as f:
        lines = f.read().split("\n")
    if any(sentence in line for line in lines):
        return {"edits": [], "create": [], "delete": [], "tests_to_run": [],
                "rationale": "Requested sentence already present in README.md"}

    title = lines[0] if lines else ""
    edit = {"path": "README.md", "search": title, "replace": f"{title}\n{sentence}"} if title else None
    if edit is None:
        return {"edits": [], "create": [{"path": "README.md", "content": sentence + "\n"}], "delete": [],
                "tests_to_run": [], "rationale": "README.md is empty; add the requested sentence."}
    return {"edits": [edit], "create": [], "delete": [], "tests_to_run": [],
            "rationale": "Insert the exact requested sentence into README.md after the title line."}


async def run_reviewer_role(ctx: RunContext) -> dict[str, Any]:
    user_content = (
        f"Task: {ctx.task}\nDiff:\n<output>\n{ctx.proposed_diff or 'No changes'}\n</output>\n"
        f"Files: {ctx.applied_files}\nValidation verdict: {getattr(ctx, 'verdict', 'unknown')}"
    )
    return await _call_role("reviewer", REVIEWER_SYSTEM, user_content, ctx=ctx)

"""Role execution - each role is an isolated model call controlled by the orchestrator."""

from __future__ import annotations

import json
import logging
from typing import Any

from patchquest.agents.prompts import (
    ANALYSIS_SYSTEM,
    CONTEXT_BUILDER_SYSTEM,
    INTAKE_SYSTEM,
    PATCH_SYSTEM,
    PLANNER_SYSTEM,
    REVIEWER_SYSTEM,
    SECURITY_SYSTEM,
)
from patchquest.agents.provider_base import ModelConfig
from patchquest.agents.provider_registry import get_provider
from patchquest.config import get_config
from patchquest.orchestrator.run_context import RunContext

logger = logging.getLogger(__name__)


async def _call_role(
    role_name: str,
    system_prompt: str,
    user_content: str,
    ctx: RunContext | None = None,
) -> dict[str, Any]:
    from patchquest.api.routes_providers import PROVIDER_CATALOG

    run_provider = ctx.provider if ctx else None
    run_model = ctx.model if ctx else None

    if run_provider and run_provider != "mock":
        catalog = next((p for p in PROVIDER_CATALOG if p["name"] == run_provider), None)
        provider = get_provider(run_provider)
        max_tokens = 4096 if run_provider == "nvidia" else 2048
        temperature = 1.0 if run_provider == "nvidia" else 0.2
        top_p = 1.0 if run_provider == "nvidia" else None
        model_config = ModelConfig(
            provider=run_provider,
            model=run_model or (catalog["default_model"] if catalog else ""),
            base_url=catalog.get("base_url") if catalog else None,
            api_key_env=catalog.get("api_key_env") if catalog else None,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
    else:
        config = get_config()
        model_profile = getattr(config.models, role_name, config.models.intake)
        provider = get_provider(model_profile.provider)
        model_config = ModelConfig(
            provider=model_profile.provider,
            model=model_profile.model,
            base_url=model_profile.base_url,
            api_key_env=model_profile.api_key_env,
            max_tokens=model_profile.max_tokens,
            temperature=model_profile.temperature,
        )

    valid, err = provider.validate_config(model_config)
    if not valid:
        raise RuntimeError(
            f"Provider '{model_config.provider}' configuration error: {err}"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await provider.complete(messages, model_config)
    except Exception as exc:
        import os
        err_msg = str(exc)
        if model_config.api_key_env:
            key_val = os.environ.get(model_config.api_key_env, "")
            if key_val:
                err_msg = err_msg.replace(key_val, "***REDACTED***")
        raise RuntimeError(
            f"LLM provider '{model_config.provider}' call failed: {err_msg}"
        ) from exc

    return _parse_json_response(response.content)


async def _call_role_text(
    role_name: str,
    system_prompt: str,
    user_content: str,
    ctx: RunContext | None = None,
) -> str:
    """Call an LLM role and return raw text (no JSON parsing)."""
    from patchquest.api.routes_providers import PROVIDER_CATALOG

    run_provider = ctx.provider if ctx else None
    run_model = ctx.model if ctx else None

    if run_provider and run_provider != "mock":
        catalog = next((p for p in PROVIDER_CATALOG if p["name"] == run_provider), None)
        provider = get_provider(run_provider)
        max_tokens = 4096 if run_provider == "nvidia" else 2048
        temperature = 1.0 if run_provider == "nvidia" else 0.2
        top_p = 1.0 if run_provider == "nvidia" else None
        model_config = ModelConfig(
            provider=run_provider,
            model=run_model or (catalog["default_model"] if catalog else ""),
            base_url=catalog.get("base_url") if catalog else None,
            api_key_env=catalog.get("api_key_env") if catalog else None,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
    else:
        config = get_config()
        model_profile = getattr(config.models, role_name, config.models.intake)
        provider = get_provider(model_profile.provider)
        model_config = ModelConfig(
            provider=model_profile.provider,
            model=model_profile.model,
            base_url=model_profile.base_url,
            api_key_env=model_profile.api_key_env,
            max_tokens=model_profile.max_tokens,
            temperature=model_profile.temperature,
        )

    valid, err = provider.validate_config(model_config)
    if not valid:
        raise RuntimeError(
            f"Provider '{model_config.provider}' configuration error: {err}"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await provider.complete(messages, model_config)
    except Exception as exc:
        import os
        err_msg = str(exc)
        if model_config.api_key_env:
            key_val = os.environ.get(model_config.api_key_env, "")
            if key_val:
                err_msg = err_msg.replace(key_val, "***REDACTED***")
        raise RuntimeError(
            f"LLM provider '{model_config.provider}' call failed: {err_msg}"
        ) from exc

    return (response.content or "").strip()


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
    from patchquest.memory.repo_map import get_repo_map
    repo_map = get_repo_map(ctx.repo_path)
    file_summary = "\n".join(f["file_path"] for f in repo_map["files"][:50])
    try:
        plan_str = json.dumps(ctx.plan, default=str)
    except (TypeError, ValueError):
        plan_str = str(ctx.plan)[:2000]
    user_content = f"Task: {ctx.task}\nPlan: {plan_str}\nFiles:\n{file_summary}"
    return await _call_role("planner", CONTEXT_BUILDER_SYSTEM, user_content, ctx=ctx)


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

    context_summary = ""
    selected = ctx.selected_context if isinstance(ctx.selected_context, dict) else {}
    for path, content in list(selected.items())[:5]:
        context_summary += f"\n--- {path} ---\n{str(content)[:2000]}\n"
    user_content = f"Task: {ctx.task}\nContext:{context_summary}"
    return await _call_role("coder", PATCH_SYSTEM, user_content, ctx=ctx)


def _build_readme_sentence_patch(repo_path: str, task: str) -> dict[str, Any] | None:
    """Build a deterministic README insert diff when task requests an exact sentence."""
    import os

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
        lines = f.readlines()

    if any(sentence in line for line in lines):
        return {
            "diff": "",
            "rationale": "Requested sentence already present in README.md",
            "files_changed": [],
            "tests_to_run": [],
        }

    insert_after = min(1, len(lines))
    anchor = lines[insert_after - 1] if insert_after > 0 else ""
    diff = (
        "--- a/README.md\n"
        "+++ b/README.md\n"
        f"@@ -{insert_after},1 +{insert_after},2 @@\n"
        f" {anchor.rstrip()}\n"
        f"+{sentence}\n"
    )
    return {
        "diff": diff,
        "rationale": "Insert the exact requested sentence into README.md after the title line.",
        "files_changed": ["README.md"],
        "tests_to_run": [],
    }


async def run_reviewer_role(ctx: RunContext) -> dict[str, Any]:
    user_content = f"Task: {ctx.task}\nDiff: {ctx.proposed_diff or 'No changes'}\nFiles: {ctx.applied_files}"
    return await _call_role("reviewer", REVIEWER_SYSTEM, user_content, ctx=ctx)

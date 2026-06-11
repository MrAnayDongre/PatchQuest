"""Mock provider for demo mode - returns deterministic, safe outputs."""

from __future__ import annotations

import json

from patchquest.agents.provider_base import ModelConfig, ProviderBase, ProviderResponse


class MockProvider(ProviderBase):
    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
        response_format: dict | None = None,
    ) -> ProviderResponse:
        last_message = messages[-1]["content"] if messages else ""

        if "intake" in config.model:
            content = json.dumps({
                "task_type": "code_change",
                "target_languages": ["python"],
                "success_criteria": "Code change applied and tests pass",
                "likely_risk": "low",
                "clarification_needed": False,
                "assumptions": ["Modifying existing code", "Tests exist for validation"],
            })
        elif "planner" in config.model:
            content = json.dumps({
                "plan": "1. Read relevant files\n2. Make minimal change\n3. Run tests",
                "files_to_inspect": ["src/main.py", "tests/test_main.py"],
                "tests_likely_needed": True,
                "expected_patch_scope": "1-2 files, <20 lines changed",
                "stop_conditions": ["Tests pass", "No regressions"],
                "test_commands": ["python -m pytest"],
            })
        elif "analyst" in config.model:
            content = (
                "Hello! Here is a high-level summary of this repository:\n\n"
                "- Backend FastAPI orchestrator with phase-based run execution\n"
                "- React frontend for launching and monitoring coding runs\n"
                "- SQLite persistence for runs, events, and reports"
            )
        elif "coder" in config.model:
            content = json.dumps({
                "diff": "",
                "rationale": "Mock provider: no actual code changes in demo mode.",
                "files_changed": [],
                "tests_to_run": ["python -m pytest"],
            })
        elif "reviewer" in config.model:
            content = json.dumps({
                "minimal_change": True,
                "unrelated_changes": False,
                "risk_notes": "No risks identified in demo mode.",
                "missing_tests": [],
                "recommendation": "approve",
            })
        elif "security" in config.model:
            content = json.dumps({
                "secret_findings": [],
                "risky_patterns": [],
                "blocked_items": [],
                "remediation": "No issues found.",
            })
        elif "memory" in config.model:
            content = json.dumps({
                "records_to_save": [],
                "records_to_invalidate": [],
            })
        else:
            content = json.dumps({
                "response": "Mock provider response for demo mode.",
                "note": "Configure a real provider for actual model inference.",
            })

        return ProviderResponse(
            content=content,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            model=config.model,
            finish_reason="stop",
        )

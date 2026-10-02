"""Scripted provider: replays predetermined role responses.

It makes the whole pipeline deterministic, which is what end-to-end tests, the evaluation
harness and run replay need. A script is keyed by the run's ``model`` string, maps a role to a
list of responses (dict/str, or a callable taking the messages), and records every request.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from patchquest.agents.provider_base import ModelConfig, ProviderBase, ProviderResponse

Response = dict | str | Callable[[list[dict[str, str]]], dict | str]

_ROLE_MARKERS = (
    ("repair", "repairing a change"),
    ("coder", "code editor"),
    ("planner", "task planner"),
    ("intake", "intake analyst"),
    ("reviewer", "code reviewer"),
    ("analyst", "read-only repository analyst"),
    ("security", "security reviewer"),
)

DEFAULTS: dict[str, dict] = {
    "intake": {"task_type": "code_change", "target_languages": [], "success_criteria": "", "likely_risk": "low",
               "clarification_needed": False, "assumptions": []},
    "planner": {"plan": "scripted", "files_to_inspect": [], "tests_likely_needed": [], "expected_patch_scope": "small",
                "stop_conditions": [], "test_commands": []},
    "reviewer": {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
                 "recommendation": "approve"},
}


@dataclass
class Script:
    responses: dict[str, list[Response]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    cursor: dict[str, int] = field(default_factory=dict)

    def calls_for(self, role: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["role"] == role]


class ScriptExhausted(RuntimeError):
    pass


def detect_role(system_prompt: str) -> str:
    lowered = system_prompt.lower()
    for role, marker in _ROLE_MARKERS:
        if marker in lowered:
            return role
    return "unknown"


class ScriptedProvider(ProviderBase):
    scripts: dict[str, Script] = {}

    @classmethod
    def register(cls, name: str, responses: dict[str, list[Response]]) -> Script:
        script = Script(responses=responses)
        cls.scripts[name] = script
        return script

    async def complete(self, messages: list[dict[str, str]], config: ModelConfig,
                       response_format: dict | None = None) -> ProviderResponse:
        script = self.scripts.get(config.model)
        if script is None:
            raise ScriptExhausted(f"no script registered for model '{config.model}'")
        role = detect_role(messages[0]["content"] if messages else "")
        script.calls.append({"role": role, "messages": messages})

        queue = script.responses.get(role)
        if queue is None:
            if role in DEFAULTS:
                content: dict | str = DEFAULTS[role]
            else:
                raise ScriptExhausted(f"script '{config.model}' has no response for role '{role}'")
        else:
            index = script.cursor.get(role, 0)
            if index >= len(queue):
                raise ScriptExhausted(f"script '{config.model}' ran out of '{role}' responses after {index}")
            script.cursor[role] = index + 1
            item = queue[index]
            content = item(messages) if callable(item) else item
        text = content if isinstance(content, str) else json.dumps(content)
        return ProviderResponse(content=text, usage={"prompt_tokens": 0, "completion_tokens": 0}, model=config.model)

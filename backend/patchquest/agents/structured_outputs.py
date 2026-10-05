"""Structured output schemas for role responses.

These are the single source of truth for what each role must return. They are used three ways:
as the JSON schema sent to engines that support constrained decoding, to validate whatever any
model returned, and (via ``coerce``) to normalise the harmless sloppiness small models produce
(a string where a list is expected, ``null`` for an empty list) without a second model call.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict


def _as_list(value: Any) -> list:
    if value is None or isinstance(value, bool):
        return []  # a bare boolean is a legacy "tests needed?" flag, not a list
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value] if value else []
    return [value]


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


StrList = Annotated[list[str], BeforeValidator(_as_list)]
Text = Annotated[str, BeforeValidator(_as_str)]


class _Out(BaseModel):
    # Lenient when validating (models add keys); the *schema we send* is strict, see json_schema_for.
    model_config = ConfigDict(extra="ignore")


class IntakeOutput(_Out):
    task_type: Text = "code_change"
    target_languages: StrList = []
    success_criteria: Text = ""
    likely_risk: Text = "low"
    clarification_needed: bool = False
    assumptions: StrList = []


class PlannerOutput(_Out):
    plan: Text = ""
    files_to_inspect: StrList = []
    tests_likely_needed: StrList = []
    expected_patch_scope: Text = ""
    stop_conditions: StrList = []
    test_commands: StrList = []


class EditOut(_Out):
    path: str
    search: Text = ""
    replace: Text = ""
    replace_all: bool = False


class CreateOut(_Out):
    path: str
    content: Text = ""


class PatchOutput(_Out):
    edits: list[EditOut] = []
    create: list[CreateOut] = []
    delete: StrList = []
    rationale: Text = ""
    tests_to_run: StrList = []
    diff: Text = ""  # legacy: a unified diff, accepted for models that prefer it


class PatchSchema(_Out):
    """What engines are *asked* for: no legacy ``diff`` escape hatch (small models take it)."""

    edits: list[EditOut] = []
    create: list[CreateOut] = []
    delete: StrList = []
    rationale: Text = ""
    tests_to_run: StrList = []


class ReviewerOutput(_Out):
    minimal_change: bool = True
    unrelated_changes: bool = False
    risk_notes: Text = ""
    missing_tests: StrList = []
    recommendation: Text = "approve"


class SecurityOutput(_Out):
    secret_findings: list[dict] = []
    risky_patterns: StrList = []
    blocked_items: StrList = []
    remediation: Text = ""


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for constrained decoding: closed objects, every property required."""
    schema = model.model_json_schema()

    def close(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for item in node:
                close(item)

    close(schema)
    return schema


def coerce(model: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """Validate ``data`` against ``model`` and return it normalised; raises ValidationError."""
    validated = model.model_validate(data).model_dump()
    return {**data, **validated}

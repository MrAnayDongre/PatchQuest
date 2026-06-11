"""Structured output schemas for role responses."""

from __future__ import annotations

from pydantic import BaseModel


class IntakeOutput(BaseModel):
    task_type: str = "code_change"
    target_languages: list[str] = []
    success_criteria: str = ""
    likely_risk: str = "low"
    clarification_needed: bool = False
    assumptions: list[str] = []


class PlannerOutput(BaseModel):
    plan: str = ""
    files_to_inspect: list[str] = []
    tests_likely_needed: bool = True
    expected_patch_scope: str = ""
    stop_conditions: list[str] = []
    test_commands: list[str] = []


class PatchOutput(BaseModel):
    diff: str = ""
    rationale: str = ""
    files_changed: list[str] = []
    tests_to_run: list[str] = []


class ReviewerOutput(BaseModel):
    minimal_change: bool = True
    unrelated_changes: bool = False
    risk_notes: str = ""
    missing_tests: list[str] = []
    recommendation: str = "approve"


class SecurityOutput(BaseModel):
    secret_findings: list[dict] = []
    risky_patterns: list[str] = []
    blocked_items: list[str] = []
    remediation: str = ""

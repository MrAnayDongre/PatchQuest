"""Phase definitions for the run state machine."""

from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    INTAKE = "intake"
    REPO_SCAN = "repo_scan"
    PLANNING = "planning"
    RESEARCH = "research"
    CONTEXT_BUILDING = "context_building"
    ANALYSIS = "analysis"
    PATCHING = "patching"
    STATIC_CHECKS = "static_checks"
    TESTING = "testing"
    REVIEW = "review"
    SECURITY_SCAN = "security_scan"
    FINAL_REPORT = "final_report"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


PHASE_ORDER = list(Phase)

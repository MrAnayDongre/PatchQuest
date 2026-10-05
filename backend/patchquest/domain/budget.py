"""Execution budgets: what a run may spend, and what it has spent.

Counters live on the run context (so they are checkpointed and survive resume); limits come from
``AgentConfig``. A limit of 0 means unlimited. This module only *describes* the position; the places
that spend (model calls, commands, the wall clock) enforce it by raising ``BUDGET_EXHAUSTED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class BudgetKind(StrEnum):
    MODEL_CALLS = "model_calls"
    TOKENS = "tokens"
    WALL_TIME = "wall_time_s"
    COMMANDS = "commands"
    PATCH_ATTEMPTS = "patch_attempts"
    REPAIR_ROUNDS = "repair_rounds"
    RETRIES = "retries"


@dataclass(frozen=True)
class BudgetLine:
    kind: BudgetKind
    used: float
    limit: float  # 0 = unlimited

    @property
    def unlimited(self) -> bool:
        return self.limit <= 0

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.used >= self.limit

    @property
    def fraction(self) -> float | None:
        return None if self.unlimited else min(1.0, self.used / self.limit)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "used": round(self.used, 2), "limit": self.limit,
                "remaining": None if self.unlimited else max(0, round(self.limit - self.used, 2)),
                "exhausted": self.exhausted}


class _Counters(Protocol):
    model_calls: int
    tokens_used: int
    wall_seconds: float
    patch_attempts: int
    repair_rounds: int
    retries: int
    commands_run: list[Any]


class _Limits(Protocol):
    max_model_calls: int
    max_total_tokens: int
    max_wall_seconds: int
    max_commands: int
    max_patch_attempts: int
    max_repair_rounds: int
    max_retries: int


def snapshot(counters: _Counters, limits: _Limits) -> list[BudgetLine]:
    return [
        BudgetLine(BudgetKind.MODEL_CALLS, counters.model_calls, limits.max_model_calls),
        BudgetLine(BudgetKind.TOKENS, counters.tokens_used, limits.max_total_tokens),
        BudgetLine(BudgetKind.WALL_TIME, counters.wall_seconds, limits.max_wall_seconds),
        BudgetLine(BudgetKind.COMMANDS, len(counters.commands_run), limits.max_commands),
        BudgetLine(BudgetKind.PATCH_ATTEMPTS, counters.patch_attempts, limits.max_patch_attempts),
        BudgetLine(BudgetKind.REPAIR_ROUNDS, counters.repair_rounds, limits.max_repair_rounds),
        BudgetLine(BudgetKind.RETRIES, counters.retries, limits.max_retries),
    ]


def exhausted(counters: _Counters, limits: _Limits, *, among: tuple[BudgetKind, ...]) -> BudgetLine | None:
    """The first exhausted line among ``among`` (the kinds the caller is about to spend), else None."""
    wanted = set(among)
    return next((line for line in snapshot(counters, limits) if line.kind in wanted and line.exhausted), None)

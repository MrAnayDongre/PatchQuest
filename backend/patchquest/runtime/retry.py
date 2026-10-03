"""The single place that decides whether and when to retry.

Callers do not loop. They run an operation through ``run_with_retry`` (or ask ``decide`` directly) and
describe it honestly: is a repeat harmless (``idempotent``), and could it already have taken effect
(``side_effect_uncertain``)? The rules, in order:

1. Never retry what retrying cannot fix (the failure table says so) or what must not repeat
   (denials, cancellation, budget, policy, deterministic invalid requests).
2. Never repeat an operation that may already have had an external effect unless it is idempotent.
3. Respect the attempt cap and the remaining retry budget.
4. Wait: exponential backoff with full jitter, or exactly what the other side asked (``Retry-After``).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from patchquest.domain.failures import Failure, FailureKind, Origin, classify

T = TypeVar("T")

# Never retried regardless of what the table says about the kind in general.
NEVER_RETRY = frozenset({
    FailureKind.COMMAND_DENIED, FailureKind.USER_CANCELLED, FailureKind.BUDGET_EXHAUSTED,
    FailureKind.INTERNAL_INVARIANT, FailureKind.REPOSITORY_DRIFT, FailureKind.MODEL_CAPABILITY,
    FailureKind.CONNECTOR_AUTH, FailureKind.MODEL_AUTH, FailureKind.REPLAY_DIVERGED, FailureKind.PATCH_PARSE, FailureKind.MODEL_INVALID_OUTPUT,
})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3  # total tries, including the first
    base_delay_s: float = 0.5
    max_delay_s: float = 30.0


DEFAULT_POLICY = RetryPolicy()


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_s: float
    reason: str


def backoff(policy: RetryPolicy, attempt: int, rng: random.Random | None = None) -> float:
    """Full-jitter exponential backoff for the delay *after* ``attempt`` (1-based) failed."""
    ceiling = min(policy.max_delay_s, policy.base_delay_s * 2 ** (attempt - 1))
    return (rng or random).uniform(0, ceiling)


def decide(failure: Failure, attempt: int, *, policy: RetryPolicy | None = None, idempotent: bool = True,
           side_effect_uncertain: bool = False, retries_left: int | None = None,
           rng: random.Random | None = None) -> RetryDecision:
    """Should we try again after ``attempt`` (1-based) failed with ``failure``?"""
    policy = policy or DEFAULT_POLICY
    if failure.kind in NEVER_RETRY or not failure.spec.retryable:
        return RetryDecision(False, 0.0, f"{failure.kind.value} is not retryable")
    if side_effect_uncertain and not idempotent:
        return RetryDecision(False, 0.0, "it may already have taken effect and is not safe to repeat")
    if attempt >= policy.max_attempts:
        return RetryDecision(False, 0.0, f"gave up after {attempt} attempts")
    if retries_left is not None and retries_left <= 0:
        return RetryDecision(False, 0.0, "the run's retry budget is used up")
    delay = failure.retry_after if failure.retry_after is not None else backoff(policy, attempt, rng)
    return RetryDecision(True, delay, f"retrying after {failure.kind.value}")


async def run_with_retry(
    operation: Callable[[], Awaitable[T]], *, policy: RetryPolicy | None = None, origin: Origin = Origin.MODEL,
    idempotent: bool = True, side_effect_uncertain: Callable[[], bool] | None = None,
    retries_left: Callable[[], int | None] | None = None,
    on_retry: Callable[[Failure, int, RetryDecision], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """Run ``operation`` and retry per ``decide``. Cancellation is never swallowed or retried."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = classify(exc, origin=origin)
            decision = decide(failure, attempt, policy=policy, idempotent=idempotent,
                              side_effect_uncertain=side_effect_uncertain() if side_effect_uncertain else False,
                              retries_left=retries_left() if retries_left else None)
            if not decision.retry:
                raise
            if on_retry:
                await on_retry(failure, attempt, decision)
            await (sleep or asyncio.sleep)(decision.delay_s)

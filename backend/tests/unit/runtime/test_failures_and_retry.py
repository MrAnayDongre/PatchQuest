"""The failure table, exception classification and the central retry rules."""

import asyncio
import json
import random
import sqlite3

import httpx
import pytest

from patchquest.domain.failures import (
    MAX_RETRY_AFTER_S,
    SPECS,
    Failure,
    FailureKind,
    Origin,
    PatchQuestError,
    classify,
    parse_retry_after,
)
from patchquest.runtime.retry import NEVER_RETRY, RetryPolicy, backoff, decide, run_with_retry


def status_error(code: int, body: str = "", headers: dict | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, text=body, headers=headers, request=req))


class TestTable:
    def test_every_kind_is_fully_described(self):
        for kind in FailureKind:
            spec = SPECS[kind]
            assert spec.message.endswith(".") and spec.recovery, kind
            assert "Traceback" not in spec.message and "_" not in spec.message  # user-facing prose, not identifiers

    def test_payload_carries_everything_the_ui_needs(self):
        payload = Failure(FailureKind.MODEL_TIMEOUT, "ReadTimeout").to_payload()
        assert {"kind", "retryable", "severity", "origin", "message", "recovery", "detail"} <= set(payload)
        assert payload["retryable"] is True and payload["origin"] == "model"

    def test_never_retry_set_agrees_with_the_table_where_it_matters(self):
        assert FailureKind.COMMAND_DENIED in NEVER_RETRY and FailureKind.USER_CANCELLED in NEVER_RETRY
        assert all(not SPECS[k].retryable or k in NEVER_RETRY for k in (FailureKind.CONNECTOR_AUTH, FailureKind.BUDGET_EXHAUSTED))


class TestClassify:
    @pytest.mark.parametrize("exc,origin,kind", [
        (httpx.ReadTimeout("t"), Origin.MODEL, FailureKind.MODEL_TIMEOUT),
        (httpx.ReadTimeout("t"), Origin.CONNECTOR, FailureKind.CONNECTOR_UNAVAILABLE),
        (httpx.ConnectError("refused"), Origin.MODEL, FailureKind.MODEL_UNAVAILABLE),
        (status_error(429), Origin.MODEL, FailureKind.MODEL_RATE_LIMIT),
        (status_error(429), Origin.CONNECTOR, FailureKind.CONNECTOR_RATE_LIMIT),
        (status_error(401), Origin.CONNECTOR, FailureKind.CONNECTOR_AUTH),
        (status_error(503), Origin.MODEL, FailureKind.MODEL_UNAVAILABLE),
        (status_error(400, "maximum context length is 4096"), Origin.MODEL, FailureKind.MODEL_CONTEXT_OVERFLOW),
        (status_error(400, "bad field"), Origin.MODEL, FailureKind.MODEL_INVALID_OUTPUT),
        (json.JSONDecodeError("x", "y", 0), Origin.MODEL, FailureKind.MODEL_INVALID_OUTPUT),
        (sqlite3.OperationalError("database is locked"), Origin.MODEL, FailureKind.DATABASE_FAILURE),
        (PermissionError("denied"), Origin.MODEL, FailureKind.ENVIRONMENT_FAILURE),
        (TimeoutError(), Origin.MODEL, FailureKind.MODEL_TIMEOUT),
    ])
    def test_known_exceptions(self, exc, origin, kind):
        assert classify(exc, origin=origin).kind is kind

    def test_unknown_exception_is_an_internal_error_and_never_retryable(self):
        failure = classify(ValueError("surprise"))
        assert failure.kind is FailureKind.INTERNAL_INVARIANT and not failure.spec.retryable

    def test_explicit_errors_pass_through(self):
        failure = classify(PatchQuestError(FailureKind.BUDGET_EXHAUSTED, "40 calls", retry_after=3))
        assert (failure.kind, failure.detail, failure.retry_after) == (FailureKind.BUDGET_EXHAUSTED, "40 calls", 3)

    def test_retry_after_is_read_from_the_response(self):
        assert classify(status_error(429, headers={"retry-after": "7"})).retry_after == 7

    @pytest.mark.parametrize("raw,expected", [("5", 5.0), ("0", 0.0), ("-3", 0.0), ("junk", None), (None, None),
                                              ("", None), ("Wed, 21 Oct 2026 07:28:00 GMT", None),
                                              ("99999", MAX_RETRY_AFTER_S)])
    def test_retry_after_parsing_is_defensive(self, raw, expected):
        assert parse_retry_after(raw) == expected


class TestDecide:
    TIMEOUT = Failure(FailureKind.MODEL_TIMEOUT, "t")

    def test_transient_failure_is_retried_with_a_delay(self):
        d = decide(self.TIMEOUT, 1)
        assert d.retry and 0 <= d.delay_s <= 0.5

    @pytest.mark.parametrize("kind", sorted(NEVER_RETRY, key=str))
    def test_never_retry_kinds_are_never_retried(self, kind):
        assert not decide(Failure(kind, "x"), 1).retry

    @pytest.mark.parametrize("kind", [k for k in FailureKind if not SPECS[k].retryable])
    def test_non_retryable_kinds_are_never_retried(self, kind):
        assert not decide(Failure(kind, "x"), 1).retry

    def test_attempt_cap(self):
        policy = RetryPolicy(max_attempts=3)
        assert decide(self.TIMEOUT, 2, policy=policy).retry
        stop = decide(self.TIMEOUT, 3, policy=policy)
        assert not stop.retry and "3 attempts" in stop.reason

    def test_uncertain_non_idempotent_effects_are_not_repeated(self):
        assert not decide(self.TIMEOUT, 1, idempotent=False, side_effect_uncertain=True).retry
        assert decide(self.TIMEOUT, 1, idempotent=True, side_effect_uncertain=True).retry
        assert decide(self.TIMEOUT, 1, idempotent=False, side_effect_uncertain=False).retry

    def test_retry_budget_is_respected(self):
        assert not decide(self.TIMEOUT, 1, retries_left=0).retry
        assert decide(self.TIMEOUT, 1, retries_left=1).retry

    def test_server_requested_delay_wins_over_backoff(self):
        d = decide(Failure(FailureKind.MODEL_RATE_LIMIT, "429", retry_after=42.0), 1)
        assert d.retry and d.delay_s == 42.0

    def test_backoff_is_bounded_and_jittered(self):
        policy = RetryPolicy(base_delay_s=1.0, max_delay_s=8.0)
        rng = random.Random(7)  # noqa: S311 - seeded for a reproducible test, not for security
        for attempt in range(1, 12):
            samples = [backoff(policy, attempt, rng) for _ in range(50)]
            ceiling = min(8.0, 2 ** (attempt - 1))
            assert all(0 <= s <= ceiling for s in samples)
            assert len(set(samples)) > 1  # jittered, not a fixed schedule


class TestRunWithRetry:
    @staticmethod
    def flaky(failures, then="ok"):
        calls = []

        async def op():
            calls.append(1)
            if len(calls) <= len(failures):
                raise failures[len(calls) - 1]
            return then

        return op, calls

    @pytest.mark.asyncio
    async def test_succeeds_after_transient_failures_and_reports_each_retry(self):
        op, calls = self.flaky([httpx.ReadTimeout("t"), status_error(503)])
        slept, seen = [], []

        async def sleep(s):
            slept.append(s)

        async def on_retry(failure, attempt, decision):
            seen.append((failure.kind, attempt))

        assert await run_with_retry(op, sleep=sleep, on_retry=on_retry) == "ok"
        assert len(calls) == 3 and len(slept) == 2
        assert seen == [(FailureKind.MODEL_TIMEOUT, 1), (FailureKind.MODEL_UNAVAILABLE, 2)]

    @pytest.mark.asyncio
    async def test_non_retryable_failure_is_raised_immediately(self):
        op, calls = self.flaky([status_error(401)] * 5)
        with pytest.raises(httpx.HTTPStatusError):
            await run_with_retry(op, sleep=lambda s: asyncio.sleep(0))
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_gives_up_after_the_cap_with_the_last_error(self):
        op, calls = self.flaky([httpx.ReadTimeout("t")] * 9)
        with pytest.raises(httpx.ReadTimeout):
            await run_with_retry(op, policy=RetryPolicy(max_attempts=4), sleep=lambda s: asyncio.sleep(0))
        assert len(calls) == 4

    @pytest.mark.asyncio
    async def test_cancellation_is_never_retried(self):
        calls = []

        async def op():
            calls.append(1)
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await run_with_retry(op, sleep=lambda s: asyncio.sleep(0))
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_uncertain_non_idempotent_operation_is_not_repeated(self):
        op, calls = self.flaky([httpx.ReadTimeout("t")] * 3)
        with pytest.raises(httpx.ReadTimeout):
            await run_with_retry(op, idempotent=False, side_effect_uncertain=lambda: True, sleep=lambda s: asyncio.sleep(0))
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retry_budget_callback_is_consulted_each_time(self):
        op, calls = self.flaky([httpx.ReadTimeout("t")] * 9)
        left = iter([2, 1, 0])
        with pytest.raises(httpx.ReadTimeout):
            await run_with_retry(op, policy=RetryPolicy(max_attempts=9), retries_left=lambda: next(left),
                                 sleep=lambda s: asyncio.sleep(0))
        assert len(calls) == 3

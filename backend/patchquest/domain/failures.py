"""One vocabulary for everything that can go wrong in a run.

Each ``FailureKind`` has a ``FailureSpec``: whether retrying can help, how serious it is, which part of
the system it came from, what to tell the user, and what they can do about it. The retry engine, the
run record and the UI all read this table, so no caller invents its own idea of "transient".
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx


class FailureKind(StrEnum):
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RATE_LIMIT = "MODEL_RATE_LIMIT"
    MODEL_CONTEXT_OVERFLOW = "MODEL_CONTEXT_OVERFLOW"
    MODEL_INVALID_OUTPUT = "MODEL_INVALID_OUTPUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_CAPABILITY = "MODEL_CAPABILITY"
    MODEL_AUTH = "MODEL_AUTH"
    CONNECTOR_AUTH = "CONNECTOR_AUTH"
    CONNECTOR_RATE_LIMIT = "CONNECTOR_RATE_LIMIT"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"
    TOOL_FAILURE = "TOOL_FAILURE"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    COMMAND_DENIED = "COMMAND_DENIED"
    POLICY_DENIED = "POLICY_DENIED"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    PATCH_PARSE = "PATCH_PARSE"
    PATCH_CONFLICT = "PATCH_CONFLICT"
    PATCH_APPLY = "PATCH_APPLY"
    PATCH_VALIDATION = "PATCH_VALIDATION"
    TEST_FAILURE = "TEST_FAILURE"
    BASELINE_FAILURE = "BASELINE_FAILURE"
    REPOSITORY_DRIFT = "REPOSITORY_DRIFT"
    SANDBOX_FAILURE = "SANDBOX_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    PLUGIN_FAILURE = "PLUGIN_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    CHECKPOINT_FAILURE = "CHECKPOINT_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    USER_CANCELLED = "USER_CANCELLED"
    REPLAY_DIVERGED = "REPLAY_DIVERGED"
    INTERNAL_INVARIANT = "INTERNAL_INVARIANT"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Origin(StrEnum):
    MODEL = "model"
    CONNECTOR = "connector"
    TOOL = "tool"
    COMMAND = "command"
    PATCH = "patch"
    TEST = "test"
    REPOSITORY = "repository"
    SANDBOX = "sandbox"
    ENVIRONMENT = "environment"
    PLUGIN = "plugin"
    STORAGE = "storage"
    BUDGET = "budget"
    USER = "user"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class FailureSpec:
    retryable: bool  # can the *same* operation, retried unchanged, plausibly succeed?
    severity: Severity
    origin: Origin
    message: str  # for people: what happened and what is affected
    recovery: tuple[str, ...]  # what they can do, in order of preference


_S, _O = Severity, Origin
SPECS: dict[FailureKind, FailureSpec] = {
    FailureKind.MODEL_TIMEOUT: FailureSpec(True, _S.WARNING, _O.MODEL, "The model took too long to answer.",
                                           ("retry", "use a faster or smaller model", "raise the model timeout")),
    FailureKind.MODEL_RATE_LIMIT: FailureSpec(True, _S.WARNING, _O.MODEL, "The model provider is rate limiting requests.",
                                              ("wait and retry", "switch provider")),
    FailureKind.MODEL_CONTEXT_OVERFLOW: FailureSpec(False, _S.ERROR, _O.MODEL,
                                                    "The prompt does not fit in the model's context window.",
                                                    ("use a model with a larger context", "narrow the task")),
    FailureKind.MODEL_INVALID_OUTPUT: FailureSpec(False, _S.ERROR, _O.MODEL,
                                                  "The model's reply could not be understood, even after a repair attempt.",
                                                  ("use a more capable model", "enable constrained output")),
    FailureKind.MODEL_UNAVAILABLE: FailureSpec(True, _S.ERROR, _O.MODEL, "The model endpoint is not reachable.",
                                               ("check that the engine is running", "retry", "switch provider")),
    FailureKind.MODEL_CAPABILITY: FailureSpec(False, _S.ERROR, _O.MODEL,
                                              "This model does not support something the task needs.",
                                              ("choose a model with the missing capability",)),
    FailureKind.MODEL_AUTH: FailureSpec(False, _S.ERROR, _O.MODEL, "The model provider rejected the credentials.",
                                        ("check the API key", "switch provider")),
    FailureKind.CONNECTOR_AUTH: FailureSpec(False, _S.ERROR, _O.CONNECTOR, "An integration's credentials were rejected.",
                                            ("reconnect the integration",)),
    FailureKind.CONNECTOR_RATE_LIMIT: FailureSpec(True, _S.WARNING, _O.CONNECTOR, "An integration is rate limiting requests.",
                                                  ("wait and retry",)),
    FailureKind.CONNECTOR_UNAVAILABLE: FailureSpec(True, _S.ERROR, _O.CONNECTOR, "An integration could not be reached.",
                                                   ("retry", "check the integration's status")),
    FailureKind.TOOL_FAILURE: FailureSpec(False, _S.ERROR, _O.TOOL, "A tool failed.", ("inspect the tool output",)),
    FailureKind.TOOL_TIMEOUT: FailureSpec(True, _S.WARNING, _O.TOOL, "A tool did not finish in time.",
                                          ("retry", "raise the tool timeout")),
    FailureKind.COMMAND_DENIED: FailureSpec(False, _S.WARNING, _O.COMMAND, "A command was not allowed to run.",
                                            ("approve it if it is safe", "change the command policy")),
    FailureKind.POLICY_DENIED: FailureSpec(False, _S.WARNING, _O.RUNTIME, "A policy does not allow this run to do something it needs.",
                                           ("ask a workspace admin to review the policy", "change the run's provider or settings")),
    FailureKind.COMMAND_TIMEOUT: FailureSpec(False, _S.WARNING, _O.COMMAND, "A command exceeded its time limit and was stopped.",
                                             ("raise the command timeout", "narrow what the command runs")),
    FailureKind.COMMAND_FAILED: FailureSpec(False, _S.INFO, _O.COMMAND, "A command exited with an error.",
                                            ("inspect the command output",)),
    FailureKind.PATCH_PARSE: FailureSpec(False, _S.ERROR, _O.PATCH, "The proposed change was malformed.",
                                         ("retry with feedback", "use a more capable model")),
    FailureKind.PATCH_CONFLICT: FailureSpec(False, _S.ERROR, _O.PATCH, "The change conflicts with the current file contents.",
                                            ("re-plan against the current files",)),
    FailureKind.PATCH_APPLY: FailureSpec(False, _S.ERROR, _O.PATCH, "The proposed change could not be applied.",
                                         ("retry with feedback", "inspect the proposed edit")),
    FailureKind.PATCH_VALIDATION: FailureSpec(False, _S.ERROR, _O.PATCH, "The change did not pass validation.",
                                              ("review the failing checks", "retry from the last checkpoint with another model")),
    FailureKind.TEST_FAILURE: FailureSpec(False, _S.INFO, _O.TEST, "Tests failed after the change.",
                                          ("inspect the failing tests",)),
    FailureKind.BASELINE_FAILURE: FailureSpec(False, _S.INFO, _O.TEST, "Tests were already failing before the change.",
                                              ("fix the existing failures first",)),
    FailureKind.REPOSITORY_DRIFT: FailureSpec(False, _S.ERROR, _O.REPOSITORY,
                                              "Files changed in the repository while the run was in progress.",
                                              ("review the changes and confirm", "start a new run")),
    FailureKind.SANDBOX_FAILURE: FailureSpec(True, _S.ERROR, _O.SANDBOX, "The isolated workspace could not be used.",
                                             ("retry", "run `patchquest doctor`")),
    FailureKind.ENVIRONMENT_FAILURE: FailureSpec(False, _S.ERROR, _O.ENVIRONMENT, "A required tool or setting is missing.",
                                                 ("run `patchquest doctor`",)),
    FailureKind.PLUGIN_FAILURE: FailureSpec(False, _S.ERROR, _O.PLUGIN, "A plugin failed and was disabled for this run.",
                                            ("check the plugin's logs",)),
    FailureKind.DATABASE_FAILURE: FailureSpec(True, _S.CRITICAL, _O.STORAGE, "PatchQuest's database could not be used.",
                                              ("retry", "check disk space and permissions")),
    FailureKind.CHECKPOINT_FAILURE: FailureSpec(False, _S.WARNING, _O.STORAGE,
                                                "A checkpoint could not be saved; the run can only resume from an earlier one.",
                                                ("check disk space",)),
    FailureKind.BUDGET_EXHAUSTED: FailureSpec(False, _S.WARNING, _O.BUDGET, "The run used up its allowed budget.",
                                              ("raise the limit and fork the run", "narrow the task")),
    FailureKind.USER_CANCELLED: FailureSpec(False, _S.INFO, _O.USER, "The run was cancelled.", ("fork it to try again",)),
    FailureKind.REPLAY_DIVERGED: FailureSpec(False, _S.WARNING, _O.RUNTIME,
                                             "The replay no longer matches the recorded run, so it was stopped.",
                                             ("compare the two runs to see where they differ",)),
    FailureKind.INTERNAL_INVARIANT: FailureSpec(False, _S.CRITICAL, _O.RUNTIME,
                                                "PatchQuest hit an internal error. Nothing was changed in your repository.",
                                                ("export the run bundle and report it",)),
}
if set(SPECS) != set(FailureKind):  # a new kind without a spec is a programming error caught at import
    raise RuntimeError(f"failure kinds without a spec: {sorted(set(FailureKind) - set(SPECS))}")


class PatchQuestError(RuntimeError):
    """An error that already knows what kind of failure it is."""

    def __init__(self, kind: FailureKind, detail: str, *, retry_after: float | None = None) -> None:
        super().__init__(detail)
        self.kind, self.detail, self.retry_after = kind, detail, retry_after


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    detail: str  # internal diagnostic; already secret-redacted by whoever builds it
    retry_after: float | None = None  # seconds the other side asked us to wait

    @property
    def spec(self) -> FailureSpec:
        return SPECS[self.kind]

    def to_payload(self) -> dict[str, Any]:
        spec = self.spec
        return {"kind": self.kind.value, "retryable": spec.retryable, "severity": spec.severity.value,
                "origin": spec.origin.value, "message": spec.message, "recovery": list(spec.recovery),
                "detail": self.detail[:2000]}


MAX_RETRY_AFTER_S = 120.0
_OVERFLOW_WORDS = ("context", "maximum", "too long", "exceed", "token limit", "max_model_len")


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` as seconds (HTTP-date form is ignored); capped so a hostile server cannot park us."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_S)


def _from_http(status: int, body: str, retry_after: float | None, *, default_origin: Origin) -> FailureKind:
    connector = default_origin is Origin.CONNECTOR
    if status in (400, 413, 422) and any(w in body.lower() for w in _OVERFLOW_WORDS):
        return FailureKind.MODEL_CONTEXT_OVERFLOW
    if status == 429:
        return FailureKind.CONNECTOR_RATE_LIMIT if connector else FailureKind.MODEL_RATE_LIMIT
    if status in (401, 403):
        return FailureKind.CONNECTOR_AUTH if connector else FailureKind.MODEL_AUTH
    if status in (408, 504):
        return FailureKind.CONNECTOR_UNAVAILABLE if connector else FailureKind.MODEL_TIMEOUT
    if status >= 500 or status in (409, 425):
        return FailureKind.CONNECTOR_UNAVAILABLE if connector else FailureKind.MODEL_UNAVAILABLE
    return FailureKind.CONNECTOR_UNAVAILABLE if connector else FailureKind.MODEL_INVALID_OUTPUT


def classify(exc: BaseException, *, origin: Origin = Origin.MODEL) -> Failure:
    """Map an exception to a ``Failure``. Unrecognised exceptions are INTERNAL_INVARIANT, never "retryable"."""
    if isinstance(exc, PatchQuestError):
        return Failure(exc.kind, exc.detail, exc.retry_after)
    if isinstance(exc, httpx.HTTPStatusError):
        resp = exc.response
        retry_after = parse_retry_after(resp.headers.get("retry-after"))
        return Failure(_from_http(resp.status_code, resp.text, retry_after, default_origin=origin),
                       f"HTTP {resp.status_code}", retry_after)
    if isinstance(exc, httpx.TimeoutException | TimeoutError | asyncio.TimeoutError):
        return Failure(FailureKind.CONNECTOR_UNAVAILABLE if origin is Origin.CONNECTOR else FailureKind.MODEL_TIMEOUT,
                       f"{type(exc).__name__}")
    if isinstance(exc, httpx.TransportError):
        return Failure(FailureKind.CONNECTOR_UNAVAILABLE if origin is Origin.CONNECTOR else FailureKind.MODEL_UNAVAILABLE,
                       f"{type(exc).__name__}: {exc}")
    if isinstance(exc, json.JSONDecodeError):
        return Failure(FailureKind.MODEL_INVALID_OUTPUT, f"invalid JSON: {exc}")
    if isinstance(exc, sqlite3.OperationalError):
        return Failure(FailureKind.DATABASE_FAILURE, str(exc))
    if isinstance(exc, PermissionError | FileNotFoundError):
        return Failure(FailureKind.ENVIRONMENT_FAILURE, f"{type(exc).__name__}: {exc}")
    return Failure(FailureKind.INTERNAL_INVARIANT, f"{type(exc).__name__}: {exc}")

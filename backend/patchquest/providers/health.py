"""What this process has observed about each model endpoint: is it answering, how fast, what went wrong.

In-memory by design: health is a fact about the present, so it is rebuilt from experience (and from
``patchquest providers --probe``) rather than persisted and trusted after a restart.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from patchquest.agents.provider_base import ModelConfig
from patchquest.domain.failures import FailureKind


@dataclass
class EndpointHealth:
    provider: str
    model: str
    base_url: str | None
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_latency_ms: int | None = None
    last_error: str | None = None  # a FailureKind value
    last_ok_at: str | None = None
    last_error_at: str | None = None

    @property
    def status(self) -> str:
        if self.consecutive_failures >= 3:
            return "down"
        if self.consecutive_failures:
            return "degraded"
        return "healthy" if self.successes else "unknown"


_LOCK = threading.Lock()
_STATE: dict[tuple[str, str, str], EndpointHealth] = {}


def _entry(config: ModelConfig) -> EndpointHealth:
    key = (config.provider, config.model, config.base_url or "")
    if key not in _STATE:
        _STATE[key] = EndpointHealth(config.provider, config.model, config.base_url)
    return _STATE[key]


def record_success(config: ModelConfig, latency_ms: int) -> None:
    with _LOCK:
        entry = _entry(config)
        entry.successes += 1
        entry.consecutive_failures = 0
        entry.last_latency_ms = latency_ms
        entry.last_ok_at = datetime.now(UTC).isoformat()


def record_failure(config: ModelConfig, kind: FailureKind) -> None:
    with _LOCK:
        entry = _entry(config)
        entry.failures += 1
        entry.consecutive_failures += 1
        entry.last_error = kind.value
        entry.last_error_at = datetime.now(UTC).isoformat()


def snapshot() -> list[dict[str, object]]:
    with _LOCK:
        return [{**asdict(e), "status": e.status} for e in _STATE.values()]


def reset() -> None:
    with _LOCK:
        _STATE.clear()

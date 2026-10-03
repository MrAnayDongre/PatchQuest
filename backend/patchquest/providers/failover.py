"""Provider failover: when switching model is allowed, and when it must be refused.

A different provider is a different privacy boundary, price and capability set. So a switch happens only
for failures that are the provider's (unavailable, timing out, rate limited - after retries are spent),
and only if it does not move data off the machine or lose a capability the run is using, unless the
operator allowed that explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from patchquest.agents.provider_base import Capabilities, ModelConfig
from patchquest.config import FailoverConfig
from patchquest.domain.failures import FailureKind
from patchquest.security import is_loopback

FAILOVER_KINDS = frozenset({FailureKind.MODEL_UNAVAILABLE, FailureKind.MODEL_TIMEOUT, FailureKind.MODEL_RATE_LIMIT})
# Run on this machine or against a fixture: no data leaves it.
LOCAL_PROVIDERS = frozenset({"mock", "scripted", "recorded", "ollama", "llamacpp", "lmstudio", "vllm", "sglang"})


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str


def is_local(config: ModelConfig) -> bool:
    """True when the model runs on this machine: a local engine on a loopback address, or a fixture."""
    if config.provider not in LOCAL_PROVIDERS:
        return False
    host = urlparse(config.base_url).hostname if config.base_url else None
    return host is None or is_loopback(host)


def check(kind: FailureKind, source: ModelConfig, source_caps: Capabilities, target: ModelConfig,
          target_caps: Capabilities, cfg: FailoverConfig, *, constrained_output_used: bool) -> Verdict:
    if kind not in FAILOVER_KINDS:
        return Verdict(False, f"{kind.value} is not a provider outage, so another provider would not help")
    if is_local(source) and not is_local(target) and not cfg.allow_cloud:
        return Verdict(False, f"{target.provider} is not on this machine; failover to a cloud model is not allowed")
    if constrained_output_used and source_caps.json_schema and not target_caps.json_schema \
            and not cfg.allow_capability_downgrade:
        return Verdict(False, f"{target.provider} cannot produce schema-constrained output, which this run was using")
    return Verdict(True, f"{source.provider} -> {target.provider}")

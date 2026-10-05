"""Where does each effective setting come from?

Layers, lowest to highest: built-in default, config file, environment variable, per-run override. Policy
ceilings then clamp the result. ``explain`` returns, per setting, the winning value and source plus every
layer it displaced, so "why is max_model_calls 20?" has a one-line answer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from patchquest.config import _ENV_OVERRIDES, AppConfig
from patchquest.domain.policy import Policy, effective_limits
from patchquest.runtime.policy import _ZERO_IS_UNLIMITED


@dataclass(frozen=True)
class Layer:
    source: str  # "default" | "file" | "env" | "run override" | "policy"
    detail: str  # the file path, variable name, policy name...
    value: Any


@dataclass
class Setting:
    key: str
    value: Any
    source: str
    detail: str
    overridden: list[Layer] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "source": self.source, "detail": self.detail,
                "overridden": [{"source": x.source, "detail": x.detail, "value": x.value} for x in self.overridden]}


def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(data, dict) and data:
        out: dict[str, Any] = {}
        for k, v in data.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: data}


def explain(config_path: str | None = None, *, run_overrides: dict[str, Any] | None = None,
            chain: list[Policy] | None = None, environ: dict[str, str] | None = None) -> list[Setting]:
    """Every setting of the effective configuration with its provenance, sorted by key."""
    env = os.environ if environ is None else environ
    path = Path(config_path or env.get("PATCHQUEST_CONFIG", "config.yaml"))
    defaults = _flatten(AppConfig().model_dump())
    file_values: dict[str, Any] = {}
    if path.exists():
        file_values = _flatten(yaml.safe_load(path.read_text()) or {})
    env_values = {field_name: (var, cast(env[var])) for var, (field_name, cast) in _ENV_OVERRIDES.items() if var in env}
    caps = effective_limits(chain or [])

    settings: list[Setting] = []
    for key in sorted(defaults):
        layers = [Layer("default", "built in", defaults[key])]
        if key in file_values:
            layers.append(Layer("file", str(path), file_values[key]))
        if key in env_values:
            var, value = env_values[key]
            layers.append(Layer("env", var, value))
        if run_overrides and key in run_overrides:
            layers.append(Layer("run override", "this run", run_overrides[key]))
        cap = caps.get(key)
        if cap is not None:
            ceiling, policy = cap
            name = key.partition(".")[2]
            current = layers[-1].value
            if (name in _ZERO_IS_UNLIMITED and current == 0) or current > ceiling:
                layers.append(Layer("policy", f"{policy.name} ({policy.scope.name.lower()})", int(ceiling)))
        winner = layers[-1]
        settings.append(Setting(key, winner.value, winner.source, winner.detail, list(reversed(layers[:-1]))))
    return settings


__all__ = ["Layer", "Setting", "explain"]

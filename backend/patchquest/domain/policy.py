"""Policy: what an action may do, decided by deterministic code, never by a model.

A *policy* is a named list of rules attached to one scope (system, organisation, workspace, repository,
workflow or user). To decide an action every applicable policy is consulted and the **strictest** answer
wins: ``DENY`` > ``REQUIRE_APPROVAL`` > ``ALLOW_WITH_LIMITS`` > ``ALLOW``. A narrower scope can therefore
tighten what a wider one allows but can never loosen it, and the built-in system floor (``SYSTEM_FLOOR``)
cannot be relaxed by any stored policy. Numeric limits merge by taking the smallest value.

Everything here is pure. Storage lives in ``persistence.policies``; enforcement points call ``evaluate``.
A policy that cannot be parsed never silently disappears: ``MALFORMED`` stands in for it and denies.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from patchquest.domain.effects import SideEffect


class Scope(IntEnum):
    """Wider scopes have smaller numbers. Ties between equally strict answers go to the wider scope."""

    SYSTEM = 0
    ORGANIZATION = 1
    WORKSPACE = 2
    REPOSITORY = 3
    WORKFLOW = 4
    USER = 5

    @classmethod
    def parse(cls, value: str) -> Scope:
        try:
            return cls[str(value).strip().upper()]
        except KeyError:
            raise PolicyError(f"unknown scope {value!r} (one of: {', '.join(s.name.lower() for s in cls)})") from None


class Result(StrEnum):
    ALLOW = "ALLOW"
    ALLOW_WITH_LIMITS = "ALLOW_WITH_LIMITS"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


_STRICTNESS = {Result.ALLOW: 0, Result.ALLOW_WITH_LIMITS: 1, Result.REQUIRE_APPROVAL: 2, Result.DENY: 3}


class PolicyError(ValueError):
    """A policy document is malformed."""


@dataclass(frozen=True)
class Rule:
    action: str  # fnmatch pattern over action ids such as "command.run" or "connector.github.*"
    result: Result
    reason: str = ""
    effects: frozenset[SideEffect] = frozenset()  # empty: any side effect
    constraints: Mapping[str, float] = field(default_factory=dict)  # for ALLOW_WITH_LIMITS; merged by minimum

    def matches(self, action: str, effect: SideEffect | None) -> bool:
        if not fnmatch.fnmatchcase(action, self.action):
            return False
        return not self.effects or (effect is not None and effect in self.effects)


@dataclass(frozen=True)
class Policy:
    name: str
    scope: Scope
    rules: tuple[Rule, ...] = ()
    limits: Mapping[str, float] = field(default_factory=dict)  # ceilings on settings, e.g. "agent.max_model_calls"
    scope_ref: str = ""  # which organisation / workspace / repository / workflow / user it is attached to
    version: int = 1
    is_malformed: bool = False

    def digest(self) -> str:
        """Content hash, recorded with runs so a decision can be tied to the exact policy text."""
        doc = [self.name, int(self.scope), self.scope_ref, self.version, sorted(self.limits.items()),
               [[r.action, r.result.value, r.reason, sorted(e.value for e in r.effects), sorted(r.constraints.items())]
                for r in self.rules]]
        return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    result: Result
    reason_code: str
    reason: str
    source_policy: str
    scope: Scope
    constraints: Mapping[str, float] = field(default_factory=dict)
    side_effect: SideEffect | None = None

    @property
    def approval_required(self) -> bool:
        return self.result is Result.REQUIRE_APPROVAL

    @property
    def allowed(self) -> bool:
        return self.result in (Result.ALLOW, Result.ALLOW_WITH_LIMITS)

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "result": self.result.value, "reason_code": self.reason_code, "reason": self.reason,
                "source_policy": self.source_policy, "scope": self.scope.name.lower(), "approval_required": self.approval_required,
                "constraints": dict(self.constraints), "side_effect": self.side_effect.value if self.side_effect else None}


def _need_approval(reason: str, *effects: SideEffect) -> Rule:
    return Rule("*", Result.REQUIRE_APPROVAL, reason, frozenset(effects))


# The floor every deployment starts from; stored policies can only add to it.
SYSTEM_FLOOR = Policy("system-floor", Scope.SYSTEM, (
    Rule("artifact.disclose:secret:*", Result.DENY, "secrets are never disclosed outside the runtime"),
    _need_approval("it changes the repository", SideEffect.REPOSITORY_WRITE),
    _need_approval("it changes something outside this machine", SideEffect.EXTERNAL_WRITE),
    _need_approval("it changes the host outside the repository", SideEffect.HOST_MUTATION),
    _need_approval("it can delete or overwrite data", SideEffect.DESTRUCTIVE),
    _need_approval("PatchQuest cannot tell what it does", SideEffect.UNKNOWN),
))

def malformed(name: str, scope: Scope, scope_ref: str, problem: str) -> Policy:
    """Stands in for a stored policy that could not be parsed: failing open would be a silent security hole."""
    return Policy(name, scope, (Rule("*", Result.DENY, f"policy {name!r} is malformed: {problem}"),), scope_ref=scope_ref,
                  is_malformed=True)


def parse_policy(doc: Mapping[str, Any], *, scope_ref: str = "", version: int = 1) -> Policy:
    """Validate a policy document (already decoded from JSON or YAML). Raises ``PolicyError``."""
    if not isinstance(doc, Mapping):
        raise PolicyError("a policy must be a mapping")
    unknown = set(doc) - {"name", "scope", "rules", "limits"}
    if unknown:
        raise PolicyError(f"unknown policy field(s): {', '.join(sorted(unknown))}")
    name = doc.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise PolicyError("a policy needs a name of 1-80 characters")
    scope = Scope.parse(doc.get("scope", ""))
    if scope is Scope.SYSTEM:
        raise PolicyError("the system floor is built in and cannot be stored")
    rules_in = doc.get("rules", [])
    if not isinstance(rules_in, list) or len(rules_in) > 200:
        raise PolicyError("rules must be a list of at most 200 entries")
    rules = []
    for i, r in enumerate(rules_in, 1):
        if not isinstance(r, Mapping) or set(r) - {"action", "result", "reason", "effects", "constraints"}:
            raise PolicyError(f"rule {i}: expected action/result/reason/effects/constraints")
        pattern = r.get("action")
        if not isinstance(pattern, str) or not pattern or len(pattern) > 120:
            raise PolicyError(f"rule {i}: action must be a non-empty pattern")
        try:
            result = Result(str(r.get("result", "")).upper())
        except ValueError:
            raise PolicyError(f"rule {i}: result must be one of {', '.join(x.value for x in Result)}") from None
        try:
            effects = frozenset(SideEffect(str(e).upper()) for e in r.get("effects", []))
        except ValueError as exc:
            raise PolicyError(f"rule {i}: {exc}") from None
        constraints = _numbers(r.get("constraints", {}), f"rule {i} constraints")
        if constraints and result is not Result.ALLOW_WITH_LIMITS:
            raise PolicyError(f"rule {i}: constraints only apply to ALLOW_WITH_LIMITS")
        if result is Result.ALLOW_WITH_LIMITS and not constraints:
            raise PolicyError(f"rule {i}: ALLOW_WITH_LIMITS needs constraints")
        rules.append(Rule(pattern, result, str(r.get("reason", ""))[:200], effects, constraints))
    return Policy(name.strip(), scope, tuple(rules), _numbers(doc.get("limits", {}), "limits"), scope_ref, version)


def _numbers(value: Any, what: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{what} must be a mapping of names to numbers")
    out = {}
    for k, v in value.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise PolicyError(f"{what}: {k!r} must be a non-negative number")
        out[str(k)] = float(v)
    return out


def evaluate(policies: Iterable[Policy], action: str, side_effect: SideEffect | None = None) -> PolicyDecision:
    """The strictest answer among the system floor and ``policies``. Within one policy the first matching rule speaks."""
    best: PolicyDecision | None = None
    limits: dict[str, float] = {}
    for policy in (SYSTEM_FLOOR, *sorted(policies, key=lambda p: p.scope)):
        rule = next((r for r in policy.rules if r.matches(action, side_effect)), None)
        if rule is None:
            continue
        code = {Result.DENY: "POLICY_DENY", Result.REQUIRE_APPROVAL: "APPROVAL_REQUIRED",
                Result.ALLOW_WITH_LIMITS: "LIMITED", Result.ALLOW: "POLICY_ALLOW"}[rule.result]
        if policy.is_malformed:
            code = "POLICY_MALFORMED"
        if rule.result is Result.ALLOW_WITH_LIMITS:
            for k, v in rule.constraints.items():
                limits[k] = min(v, limits.get(k, v))
        decision = PolicyDecision(action, rule.result, code, rule.reason, policy.name, policy.scope, side_effect=side_effect)
        if best is None or _STRICTNESS[decision.result] > _STRICTNESS[best.result]:
            best = decision
    if best is None:
        return PolicyDecision(action, Result.ALLOW, "NO_RESTRICTION", "no policy restricts this action", SYSTEM_FLOOR.name,
                              Scope.SYSTEM, side_effect=side_effect)
    if limits and best.result is Result.ALLOW_WITH_LIMITS:
        best = PolicyDecision(best.action, best.result, best.reason_code, best.reason, best.source_policy, best.scope,
                              limits, best.side_effect)
    return best


def strictest(decisions: Iterable[PolicyDecision]) -> PolicyDecision:
    """The most restrictive of several decisions (the first one wins a tie)."""
    return max(decisions, key=lambda d: _STRICTNESS[d.result])


def effective_limits(policies: Iterable[Policy]) -> dict[str, tuple[float, Policy]]:
    """Per setting, the smallest ceiling any policy sets, with the policy that set it."""
    out: dict[str, tuple[float, Policy]] = {}
    for policy in sorted(policies, key=lambda p: p.scope):
        for key, value in policy.limits.items():
            if key not in out or value < out[key][0]:
                out[key] = (value, policy)
    return out

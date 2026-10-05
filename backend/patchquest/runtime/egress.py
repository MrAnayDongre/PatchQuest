"""Policy at the points where data leaves the runtime: network reads and artifact disclosure.

Callers ask *before* the outbound call and obey. A boundary that cannot ask a person (a search request, a
model call, an export) treats REQUIRE_APPROVAL as a refusal, so nothing leaves on a maybe. The vocabulary and
action ids are in ``domain.egress``; the rules are ordinary policy rules.
"""

from __future__ import annotations

from collections.abc import Iterable

from patchquest.domain.effects import SideEffect
from patchquest.domain.egress import disclosure_actions, network_read_action
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.domain.policy import Policy, PolicyDecision, Result, Scope, strictest
from patchquest.runtime import policy as policy_runtime


def decide_network_read(chain: list[Policy], url_or_host: str) -> PolicyDecision:
    return policy_runtime.decide(chain, network_read_action(url_or_host), SideEffect.NETWORK_READ)


def decide_disclosure(chain: list[Policy], data_classes: Iterable[str], kind: str, name: str | None = None) -> PolicyDecision:
    """The strictest answer over every class being disclosed. An unknown class or destination is refused."""
    decisions: list[PolicyDecision] = []
    for data_class in data_classes:
        try:
            actions = disclosure_actions(data_class, kind, name)
        except ValueError as exc:
            return PolicyDecision(f"artifact.disclose:{data_class}:{kind}", Result.DENY, "UNKNOWN_DISCLOSURE", str(exc),
                                  "system-floor", Scope.SYSTEM)
        decisions.extend(policy_runtime.decide(chain, a) for a in actions)
    if not decisions:
        return PolicyDecision(f"artifact.disclose:-:{kind}", Result.ALLOW, "NOTHING_DISCLOSED", "no data leaves", "system-floor", Scope.SYSTEM)
    return strictest(decisions)


def enforce(decision: PolicyDecision, what: str) -> PolicyDecision:
    """Raise ``POLICY_DENIED`` unless the decision allows ``what`` outright."""
    if decision.allowed:
        return decision
    why = decision.reason or decision.reason_code
    if decision.approval_required:
        why += " (needs an approval, which this step cannot request)"
    raise PatchQuestError(FailureKind.POLICY_DENIED,
                          f"{what} is not allowed by policy '{decision.source_policy}' ({decision.scope.name.lower()} scope): {why}")

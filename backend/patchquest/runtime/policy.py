"""Policy at the points where PatchQuest acts: look up the chain, decide, clamp limits, explain.

The pure rules are in ``domain.policy`` and storage in ``persistence.policies``; this module joins them to
the run being executed. Callers ask ``decide`` and obey the answer; nothing here (or anywhere) lets model
output change a policy.
"""

from __future__ import annotations

from typing import Any

from patchquest.config import AgentConfig, get_config
from patchquest.database import get_db
from patchquest.domain.effects import SideEffect
from patchquest.domain.policy import Policy, PolicyDecision, PolicyError, Scope, effective_limits, evaluate
from patchquest.persistence import identity, policies

# Settings where 0 means "no limit": a ceiling still applies to them.
_ZERO_IS_UNLIMITED = frozenset({"max_total_tokens", "max_retries", "max_wall_seconds", "max_commands"})


def chain_for(*, workspace_id: str, repo_path: str | None = None, workflow_id: str | None = None,
              user: str | None = None) -> list[Policy]:
    with get_db() as conn:
        return policies.load_chain(conn, workspace_id=workspace_id, repo_path=repo_path, workflow_id=workflow_id, user=user)


def decide(chain: list[Policy], action: str, side_effect: SideEffect | None = None) -> PolicyDecision:
    return evaluate(chain, action, side_effect)


def validate_limits(limits: dict[str, float]) -> None:
    for key in limits:
        section, _, name = key.partition(".")
        if section != "agent" or name not in AgentConfig.model_fields or not isinstance(getattr(AgentConfig(), name), int):
            raise PolicyError(f"limit {key!r} is not a numeric agent setting (use agent.<setting>, e.g. agent.max_model_calls)")


def store(doc: dict[str, Any], *, scope_ref: str, actor: str, org_id: str | None = None,
          workspace_id: str | None = None) -> Policy:
    """Validate and save a policy, and record who changed what in the audit log."""
    with get_db() as conn:
        policy = policies.put(conn, doc, scope_ref=scope_ref, actor=actor)
        validate_limits(dict(policy.limits))
        identity.audit(conn, "policy.put", actor=actor, org_id=org_id, workspace_id=workspace_id,
                       target=f"{policy.scope.name.lower()}:{scope_ref}:{policy.name}",
                       detail={"version": policy.version, "digest": policy.digest()})
    return policy


def clamp_overrides(overrides: dict[str, Any] | None, chain: list[Policy]) -> dict[str, Any] | None:
    """Per-run overrides with every policy ceiling applied. A run can ask for less than a ceiling, never more;
    a ceiling below the global default is applied even when the run asked for nothing."""
    caps = effective_limits(chain)
    if not caps:
        return overrides
    base = get_config().agent
    out = dict(overrides or {})
    for key, (cap, _policy) in caps.items():
        name = key.partition(".")[2]
        wanted = out.get(key, getattr(base, name))
        if name in _ZERO_IS_UNLIMITED and wanted == 0:
            out[key] = int(cap)
        elif wanted > cap:
            out[key] = int(cap)
    return out or None


def effective_chain_description(chain: list[Policy]) -> list[dict[str, Any]]:
    return [{"name": p.name, "scope": p.scope.name.lower(), "scope_ref": p.scope_ref, "version": p.version,
             "digest": p.digest(), "malformed": p.is_malformed} for p in sorted(chain, key=lambda p: p.scope)]


__all__ = ["Scope", "chain_for", "clamp_overrides", "decide", "effective_chain_description", "store", "validate_limits"]

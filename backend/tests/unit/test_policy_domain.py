"""Policy semantics: strictest answer wins, narrower scopes cannot loosen, bad documents never fail open."""

import pytest

from patchquest.domain.effects import SideEffect
from patchquest.domain.policy import PolicyError, Result, Scope, effective_limits, evaluate, malformed, parse_policy


def policy(scope, *rules, name="p", limits=None, ref="x"):
    return parse_policy({"name": name, "scope": scope, "rules": list(rules), "limits": limits or {}}, scope_ref=ref)


def deny(action="command.run", **kw):
    return {"action": action, "result": "DENY", "reason": "no", **kw}


def test_nothing_restricts_a_read_only_action():
    d = evaluate([], "command.run", SideEffect.READ_ONLY)
    assert d.result is Result.ALLOW and d.reason_code == "NO_RESTRICTION"


@pytest.mark.parametrize("effect", [SideEffect.REPOSITORY_WRITE, SideEffect.EXTERNAL_WRITE, SideEffect.HOST_MUTATION,
                                    SideEffect.DESTRUCTIVE, SideEffect.UNKNOWN])
def test_the_system_floor_asks_for_dangerous_effects_with_no_stored_policy(effect):
    d = evaluate([], "connector.github.create_pull_request", effect)
    assert d.approval_required and d.scope is Scope.SYSTEM and d.source_policy == "system-floor"


def test_a_deny_in_a_wider_scope_cannot_be_allowed_by_a_narrower_one():
    org = policy("organization", deny())
    user = policy("user", {"action": "command.run", "result": "ALLOW"})
    d = evaluate([user, org], "command.run", SideEffect.READ_ONLY)
    assert d.result is Result.DENY and d.scope is Scope.ORGANIZATION


def test_a_narrower_scope_can_tighten():
    ws = policy("workspace", {"action": "command.run", "result": "ALLOW"})
    wf = policy("workflow", {"action": "command.*", "result": "REQUIRE_APPROVAL", "reason": "release flow"})
    d = evaluate([ws, wf], "command.run", SideEffect.WORKSPACE_WRITE)
    assert d.approval_required and d.scope is Scope.WORKFLOW


def test_the_floor_cannot_be_relaxed_by_an_allow():
    allow_all = policy("workspace", {"action": "*", "result": "ALLOW"})
    assert evaluate([allow_all], "x", SideEffect.EXTERNAL_WRITE).approval_required


def test_equally_strict_answers_are_attributed_to_the_widest_scope():
    a, b = policy("user", deny(), name="mine"), policy("organization", deny(), name="corp")
    assert evaluate([a, b], "command.run").source_policy == "corp"


def test_the_first_matching_rule_within_a_policy_speaks_and_effects_filter():
    p = policy("workspace", deny(effects=["EXTERNAL_WRITE"]), {"action": "command.run", "result": "ALLOW"})
    assert evaluate([p], "command.run", SideEffect.EXTERNAL_WRITE).result is Result.DENY
    assert evaluate([p], "command.run", SideEffect.READ_ONLY).result is Result.ALLOW
    assert evaluate([p], "command.run", None).result is Result.ALLOW  # an effect filter never matches an unknown effect


def test_limits_merge_to_the_smallest_ceiling_and_name_their_source():
    org = policy("organization", name="corp", limits={"agent.max_model_calls": 30})
    ws = policy("workspace", name="team", limits={"agent.max_model_calls": 20, "agent.max_commands": 10})
    got = effective_limits([ws, org])
    assert got["agent.max_model_calls"][0] == 20 and got["agent.max_model_calls"][1].name == "team"
    assert got["agent.max_commands"][0] == 10


def test_allow_with_limits_merges_constraints_by_minimum():
    a = policy("organization", {"action": "net.*", "result": "ALLOW_WITH_LIMITS", "constraints": {"timeout_seconds": 30}}, name="a")
    b = policy("workspace", {"action": "net.*", "result": "ALLOW_WITH_LIMITS", "constraints": {"timeout_seconds": 10}}, name="b")
    d = evaluate([a, b], "net.fetch", SideEffect.NETWORK_READ)
    assert d.result is Result.ALLOW_WITH_LIMITS and d.constraints == {"timeout_seconds": 10}


def test_a_malformed_stored_policy_denies_everything():
    d = evaluate([malformed("bad", Scope.WORKSPACE, "ws", "bad json")], "command.run", SideEffect.READ_ONLY)
    assert d.result is Result.DENY and d.reason_code == "POLICY_MALFORMED"


@pytest.mark.parametrize("doc", [
    [], {"scope": "workspace"}, {"name": "n", "scope": "planet"}, {"name": "n", "scope": "system"},
    {"name": "n", "scope": "user", "rules": [{"action": "x", "result": "MAYBE"}]},
    {"name": "n", "scope": "user", "rules": [{"action": "x", "result": "ALLOW", "constraints": {"a": 1}}]},
    {"name": "n", "scope": "user", "rules": [{"action": "x", "result": "ALLOW_WITH_LIMITS"}]},
    {"name": "n", "scope": "user", "rules": [{"action": "x", "result": "DENY", "effects": ["FLYING"]}]},
    {"name": "n", "scope": "user", "limits": {"agent.max_commands": -1}},
    {"name": "n", "scope": "user", "limits": {"agent.max_commands": True}},
    {"name": "n", "scope": "user", "surprise": 1},
])
def test_malformed_documents_are_refused(doc):
    with pytest.raises(PolicyError):
        parse_policy(doc)


def test_the_digest_changes_with_content_only():
    a = policy("user", deny())
    assert a.digest() == policy("user", deny()).digest()
    assert a.digest() != policy("user", {**deny(), "reason": "other"}).digest()

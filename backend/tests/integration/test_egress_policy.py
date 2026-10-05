"""Policy on data leaving the runtime: network reads and artifact disclosure, enforced before the outbound call."""

from __future__ import annotations

import pytest

from patchquest import cli
from patchquest.database import get_db
from patchquest.domain.effects import SideEffect
from patchquest.domain.egress import disclosure_actions, network_read_action
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.policy import Result, Rule, parse_policy
from patchquest.persistence import identity
from patchquest.runtime import egress
from patchquest.runtime import policy as pol
from tests.support import FIX, PLAN, fetch_events, make_calc_repo, run_scripted


def store(*rules, name="egress", scope_ref=LOCAL_WORKSPACE_ID, scope="workspace"):
    pol.store({"name": name, "scope": scope, "rules": list(rules)}, scope_ref=scope_ref, actor="user:admin")


def chain(**kw):
    return pol.chain_for(workspace_id=kw.pop("workspace_id", LOCAL_WORKSPACE_ID), **kw)


def test_hosts_are_normalised_and_unknown_vocabulary_is_refused():
    assert network_read_action("https://User:pw@Docs.GitHub.com:443/a/b") == "network.read.domain:docs.github.com"
    assert network_read_action("pypi.org") == "network.read.domain:pypi.org"
    assert disclosure_actions("diff", "connector", "GitHub") == ("artifact.disclose:diff:connector", "artifact.disclose:diff:github")
    with pytest.raises(ValueError):
        disclosure_actions("everything", "connector")
    d = egress.decide_disclosure([], ["diff"], "carrier_pigeon")
    assert d.result is Result.DENY and d.reason_code == "UNKNOWN_DISCLOSURE"


def test_with_no_policy_reads_and_ordinary_disclosure_are_open_but_secrets_are_not():
    assert egress.decide_network_read([], "https://pypi.org/simple").allowed
    assert egress.decide_disclosure([], ["diff", "logs"], "connector", "github").allowed
    d = egress.decide_disclosure([], ["secret"], "connector", "github")
    assert d.result is Result.DENY and d.source_policy == "system-floor"


def test_no_stored_policy_can_make_a_secret_disclosable():
    store({"action": "artifact.disclose:*", "result": "ALLOW"}, {"action": "*", "result": "ALLOW"})
    for kind in ("model_provider", "connector", "portable_bundle", "external_api"):
        assert egress.decide_disclosure(chain(), ["secret"], kind).result is Result.DENY


def test_an_allowlist_is_a_policy_with_allow_rules_before_a_catch_all_deny():
    store({"action": "network.read.domain:docs.github.com", "result": "ALLOW"},
          {"action": "network.read.domain:*.pypi.org", "result": "ALLOW"},
          {"action": "network.read.domain:*", "result": "DENY", "reason": "destination is not permitted by effective policy"})
    ok = egress.decide_network_read(chain(), "https://docs.github.com/en")
    assert ok.allowed and ok.source_policy == "egress" and ok.side_effect is SideEffect.NETWORK_READ
    assert egress.decide_network_read(chain(), "files.pypi.org").allowed
    bad = egress.decide_network_read(chain(), "http://unknown.example/x")
    assert bad.result is Result.DENY and bad.reason == "destination is not permitted by effective policy"
    # the first matching rule speaks within one policy, but a different policy's deny still wins over this allow
    store({"action": "network.read.domain:docs.github.com", "result": "DENY", "reason": "no docs"}, name="repo-tighter",
          scope="repository", scope_ref="/srv/r")
    assert egress.decide_network_read(chain(repo_path="/srv/r"), "docs.github.com").result is Result.DENY


def test_a_lower_scope_cannot_weaken_a_higher_one():
    store({"action": "artifact.disclose:source_code:*", "result": "DENY", "reason": "code stays inside"})
    store({"action": "artifact.disclose:*", "result": "ALLOW"}, name="repo-loosens", scope="repository", scope_ref="/srv/r")
    d = egress.decide_disclosure(chain(repo_path="/srv/r"), ["source_code"], "model_provider", "openai")
    assert d.result is Result.DENY and d.source_policy == "egress"


def test_a_destination_rule_applies_by_kind_or_by_name_and_the_stricter_wins():
    store({"action": "artifact.disclose:diff:github", "result": "DENY", "reason": "no diffs to github"},
          {"action": "artifact.disclose:logs:connector", "result": "REQUIRE_APPROVAL", "reason": "logs need a look"})
    assert egress.decide_disclosure(chain(), ["diff"], "connector", "github").result is Result.DENY
    assert egress.decide_disclosure(chain(), ["diff"], "connector", "slack").allowed
    assert egress.decide_disclosure(chain(), ["logs"], "connector", "slack").result is Result.REQUIRE_APPROVAL
    with pytest.raises(PatchQuestError, match="needs an approval, which this step cannot request"):
        egress.enforce(egress.decide_disclosure(chain(), ["logs"], "connector", "slack"), "posting")


def test_one_tenants_egress_policy_does_not_apply_to_another():
    with get_db() as conn:
        ws2 = identity.create_workspace(conn, identity.create_org(conn, "other"), "other-ws")
    store({"action": "*", "result": "DENY", "reason": "frozen"}, scope_ref=ws2, name="theirs")
    assert egress.decide_network_read(chain(workspace_id=ws2), "pypi.org").result is Result.DENY
    assert egress.decide_network_read(chain(), "pypi.org").allowed


def test_the_explanation_names_the_action_decision_and_source(capsys):
    store({"action": "network.read.domain:*", "result": "DENY", "reason": "destination is not permitted by effective policy"},
          name="ws-net")
    code = cli.main(["policy", "explain", "network.read.domain:unknown.example", "--effect", "NETWORK_READ"])
    out = capsys.readouterr().out
    assert code == 0 and "network.read.domain:unknown.example" in out and "DENY" in out
    assert "destination is not permitted by effective policy" in out and "ws-net (workspace scope)" in out


# ------------------------------------------------------------------ enforcement before the outbound call
class FakeProvider:
    name = "fake"
    base_url = "https://api.tracker.test/search"
    calls = 0

    async def search(self, query, options=None):
        from patchquest.search.search_models import SearchResponse

        type(self).calls += 1
        return SearchResponse(query=query, provider="fake", retrieved_at="2026-01-01T00:00:00Z", results=[])


async def test_a_denied_search_host_is_never_contacted(monkeypatch):
    from patchquest.search import search_service

    FakeProvider.calls = 0
    monkeypatch.setattr(search_service, "get_search_provider", lambda name, **kw: FakeProvider())
    store({"action": "network.read.domain:api.tracker.test", "result": "DENY", "reason": "no trackers"})
    from patchquest.search.search_models import SearchOptions

    with pytest.raises(PatchQuestError) as exc:
        await search_service.search("q", "fake", SearchOptions(force_refresh=True), policy_chain=chain())
    assert exc.value.kind is FailureKind.POLICY_DENIED and FakeProvider.calls == 0
    ok = await search_service.search("q", "fake2", SearchOptions(force_refresh=True), policy_chain=[])
    assert ok.error is None and FakeProvider.calls == 1


@pytest.fixture
async def finished(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX]})
    return rid


def test_export_is_refused_by_policy_before_a_file_is_written(finished, tmp_path):
    store({"action": "artifact.disclose:source_code:portable_bundle", "result": "DENY", "reason": "code stays inside"})
    dest = tmp_path / "out.zip"
    assert cli.main(["export", finished, str(dest), "--code"]) == cli.EXIT_FAILED
    assert not dest.exists()
    assert cli.main(["export", finished, str(tmp_path / "meta.zip")]) == cli.EXIT_OK  # metadata and logs only: still allowed


async def test_a_hosted_model_provider_does_not_get_source_code_the_policy_withholds(tmp_path):
    from tests.support import prepare_run

    store({"action": "artifact.disclose:source_code:model_provider", "result": "DENY", "reason": "no source to hosted models"})
    sm, rid = prepare_run(make_calc_repo(tmp_path / "r"), {})
    sm.ctx.provider = "openai"
    with pytest.raises(PatchQuestError, match="sending source code to openai"):
        await sm._check_model_policy()
    assert any(e["type"] == "model_denied" for e in fetch_events(rid))
    sm.ctx.provider = "ollama"  # local providers keep the code on this machine, so the rule does not apply
    await sm._check_model_policy()


def test_rule_objects_still_compose_with_the_floor():
    p = parse_policy({"name": "x", "scope": "workspace", "rules": [{"action": "artifact.disclose:secret:*", "result": "ALLOW"}]})
    assert isinstance(p.rules[0], Rule)
    assert egress.decide_disclosure([p], ["secret"], "connector").result is Result.DENY

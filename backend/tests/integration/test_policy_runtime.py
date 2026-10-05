"""Stored policies: scoping, versioning, fail-closed loading, ceilings on per-run limits, and config provenance."""

import pytest

from patchquest.application import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.config_explain import explain
from patchquest.database import get_db
from patchquest.domain.effects import SideEffect
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.policy import PolicyError, Result, Scope
from patchquest.persistence import identity, policies
from patchquest.runtime import policy as pol
from tests.support import make_calc_repo


def doc(name="corp", scope="workspace", **kw):
    return {"name": name, "scope": scope, **kw}


def test_versions_are_kept_and_only_the_latest_is_active():
    pol.store(doc(rules=[{"action": "command.run", "result": "DENY"}]), scope_ref=LOCAL_WORKSPACE_ID, actor="user:a")
    second = pol.store(doc(rules=[{"action": "command.run", "result": "ALLOW"}]), scope_ref=LOCAL_WORKSPACE_ID, actor="user:a")
    assert second.version == 2
    with get_db() as conn:
        assert [p.version for p in policies.list_policies(conn)] == [2]
        assert conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0] == 2
        with pytest.raises(Exception, match="keep their history"):
            conn.execute("DELETE FROM policies")


def test_a_chain_holds_only_the_policies_attached_to_this_run_context():
    with get_db() as conn:
        org2 = identity.create_org(conn, "other")
        ws2 = identity.create_workspace(conn, org2, "other-ws")
    pol.store(doc(rules=[{"action": "x", "result": "DENY"}]), scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    pol.store(doc("theirs", rules=[{"action": "x", "result": "DENY"}]), scope_ref=ws2, actor="a")
    pol.store(doc("repo-rule", "repository"), scope_ref="/srv/repo", actor="a")
    names = lambda chain: sorted(p.name for p in chain)  # noqa: E731
    assert names(pol.chain_for(workspace_id=LOCAL_WORKSPACE_ID)) == ["corp"]
    assert names(pol.chain_for(workspace_id=LOCAL_WORKSPACE_ID, repo_path="/srv/repo")) == ["corp", "repo-rule"]
    assert names(pol.chain_for(workspace_id=ws2)) == ["theirs"]


def test_a_corrupt_stored_row_denies_instead_of_vanishing():
    pol.store(doc(), scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    with get_db() as conn:
        conn.execute("UPDATE policies SET document_json = '{not json'")
    d = pol.decide(pol.chain_for(workspace_id=LOCAL_WORKSPACE_ID), "command.run", SideEffect.READ_ONLY)
    assert d.result is Result.DENY and d.reason_code == "POLICY_MALFORMED"


def test_storing_is_audited_and_bad_documents_store_nothing():
    pol.store(doc(), scope_ref=LOCAL_WORKSPACE_ID, actor="user:ana")
    with pytest.raises(PolicyError):
        pol.store(doc("bad", limits={"agent.no_such_setting": 1}), scope_ref=LOCAL_WORKSPACE_ID, actor="user:ana")
    with pytest.raises(PolicyError):
        pol.store(doc("bad2", limits={"safety.max_command_timeout": 1}), scope_ref=LOCAL_WORKSPACE_ID, actor="user:ana")
    with get_db() as conn:
        assert [p.name for p in policies.list_policies(conn)] == ["corp"]
        assert [r["action"] for r in identity.read_audit(conn, None)] == ["policy.put"]


def test_a_ceiling_caps_a_runs_overrides_and_the_global_default(tmp_path):
    pol.store(doc(limits={"agent.max_model_calls": 5, "agent.max_commands": 7}), scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    repo = make_calc_repo(tmp_path / "repo")
    svc = TaskService()
    asked_more = svc.create_run(repo_path=str(repo), task="t", overrides={"agent.max_model_calls": 99})
    assert asked_more["overrides_json"] and '"agent.max_model_calls": 5' in asked_more["overrides_json"]
    asked_less = svc.create_run(repo_path=str(repo), task="t", overrides={"agent.max_model_calls": 2})
    assert '"agent.max_model_calls": 2' in asked_less["overrides_json"]
    assert '"agent.max_commands": 7' in asked_less["overrides_json"]  # default is 60: capped even though not asked


def test_unlimited_settings_are_capped_too():
    pol.store(doc(limits={"agent.max_total_tokens": 1000}), scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    out = pol.clamp_overrides(None, pol.chain_for(workspace_id=LOCAL_WORKSPACE_ID))
    assert out == {"agent.max_total_tokens": 1000}  # the default 0 means unlimited


def test_explain_shows_value_source_and_what_it_displaced(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("agent:\n  max_model_calls: 30\n")
    pol.store(doc("corp-cap", limits={"agent.max_model_calls": 20}), scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    chain = pol.chain_for(workspace_id=LOCAL_WORKSPACE_ID)
    by_key = {s.key: s for s in explain(str(cfg), run_overrides={"agent.max_model_calls": 25}, chain=chain, environ={})}
    s = by_key["agent.max_model_calls"]
    assert (s.value, s.source) == (20, "policy") and "corp-cap" in s.detail
    assert [(x.source, x.value) for x in s.overridden] == [("run override", 25), ("file", 30), ("default", 40)]
    assert by_key["agent.max_commands"].source == "default"


def test_explain_reads_the_environment_layer(tmp_path):
    s = {x.key: x for x in explain(str(tmp_path / "none.yaml"), environ={"PATCHQUEST_PORT": "9001"})}["port"]
    assert (s.value, s.source, s.detail) == (9001, "env", "PATCHQUEST_PORT")
    assert [x.source for x in s.overridden] == ["default"]


def test_explain_agrees_with_the_config_that_actually_loads(tmp_path, monkeypatch):
    from patchquest.config import load_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text("agent:\n  max_commands: 12\nsafety:\n  allow_network: true\n")
    monkeypatch.setenv("PATCHQUEST_PORT", "9100")
    loaded = load_config(str(cfg)).model_dump()
    for s in explain(str(cfg)):
        node = loaded
        for part in s.key.split("."):
            node = node[part]
        assert node == s.value, s.key
    set_config(AppConfig())


def test_scope_parse_names_the_valid_ones():
    with pytest.raises(PolicyError, match="organization"):
        Scope.parse("nope")


def test_a_fork_cannot_raise_a_limit_above_the_ceiling(tmp_path):
    repo = make_calc_repo(tmp_path / "repo")
    svc = TaskService()
    parent = svc.create_run(repo_path=str(repo), task="t")
    pol.store(doc(limits={"agent.max_model_calls": 5}), scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    from patchquest.runtime import lineage
    with get_db() as conn:
        child = lineage.create_child(conn, parent["id"], kind="fork", parent_cp=None, overrides={"agent.max_model_calls": 500})
        row = conn.execute("SELECT overrides_json FROM runs WHERE id = ?", (child,)).fetchone()
    assert '"agent.max_model_calls": 5' in row["overrides_json"]

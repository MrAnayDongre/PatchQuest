"""The memory, preferences, repo and explain commands through the real parser."""

import json

from patchquest.cli import main
from tests.integration.test_memory import make_repo


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_memory_add_list_show_forget(tmp_path, capsys):
    repo = tmp_path / "r"
    repo.mkdir()
    code, out, _ = run(capsys, "memory", "add", "build.note", "uses poetry", "--ref", str(repo), "--json")
    assert code == 0
    mid = json.loads(out)["id"]
    code, out, _ = run(capsys, "memory", "list", "--repo", str(repo), "--json")
    [row] = json.loads(out)
    assert row["key"] == "build.note" and row["source"] == "user_explicit" and row["trusted"] is True
    code, out, _ = run(capsys, "memory", "show", mid[:8])
    assert code == 0 and "build.note" in out and "history" in out
    assert run(capsys, "memory", "forget", mid)[0] == 0
    assert json.loads(run(capsys, "memory", "list", "--repo", str(repo), "--json")[1]) == []
    assert any(r["status"] == "forgotten" for r in json.loads(run(capsys, "memory", "list", "--repo", str(repo), "--all", "--json")[1]))


def test_secrets_and_bad_scopes_are_refused_with_a_reason(tmp_path, capsys):
    code, _, err = run(capsys, "memory", "add", "k", "token sk-ant-api03-" + "q" * 40, "--ref", str(tmp_path))
    assert code == 1 and "secret" in err and "sk-ant" not in err
    code, _, err = run(capsys, "memory", "add", "k", "v", "--scope", "user")
    assert code == 1 and "needs" in err


def test_preferences_set_list_unset_and_unknown_keys(tmp_path, capsys):
    ref = str(tmp_path)
    assert run(capsys, "preferences", "set", "test.commands", '["python3 -m pytest -q"]', "--scope", "repository", "--ref", ref)[0] == 0
    assert run(capsys, "preferences", "set", "automation.external_writes", "auto", "--scope", "workspace")[0] == 0
    code, out, _ = run(capsys, "preferences", "list", "--repo", ref, "--json")
    got = json.loads(out)
    assert got["test.commands"]["value"] == ["python3 -m pytest -q"] and got["test.commands"]["decided_by"]["scope"] == "repository"
    assert got["automation.external_writes"]["value"] == "auto"
    assert run(capsys, "preferences", "set", "deploy.to.prod", "true")[2].startswith("error: ")
    assert run(capsys, "preferences", "unset", "test.commands", "--scope", "repository", "--ref", ref)[0] == 0
    assert json.loads(run(capsys, "preferences", "list", "--repo", ref, "--json")[1])["test.commands"]["decided_by"] == "system default"


def test_repo_profile_refresh_and_correction(tmp_path, capsys):
    repo = make_repo(tmp_path / "r")
    code, out, _ = run(capsys, "repo", "profile", str(repo), "--json")
    data = json.loads(out)
    assert data["refresh"]["fast_path"] is False and data["profile"]["ci_provider"]["value"] == "github_actions"
    assert run(capsys, "repo", "set", "ci_provider", '"buildkite"', "--path", str(repo))[0] == 0
    code, out, _ = run(capsys, "repo", "profile", str(repo), "--refresh", "--json")
    ci = json.loads(out)["profile"]["ci_provider"]
    assert ci["value"] == "buildkite" and ci["source"] == "user_explicit"
    assert run(capsys, "repo", "set", "not_a_field", "1", "--path", str(repo))[0] == 1


def test_explain_on_a_run_with_nothing_to_say(capsys):
    from tests.support.db import insert_run
    insert_run("r-quiet")
    code, out, _ = run(capsys, "explain", "r-quiet")
    assert code == 0 and "nothing in this run" in out

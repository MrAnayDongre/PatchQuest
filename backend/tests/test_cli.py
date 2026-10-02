"""CLI behaviour: exit codes, machine-readable output, approvals, doctor."""

import json

import pytest

from patchquest import cli
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.config import AppConfig, set_config
from patchquest.doctor import FAIL, run_checks

BUG = "def add(a, b):\n    return a - b\n"
TEST = (
    "import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
    "from calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
)
CMD = "python3 -m unittest discover -s tests -q"
PLAN = {"plan": "fix", "files_to_inspect": ["calc.py"], "tests_likely_needed": [], "expected_patch_scope": "1 file",
        "stop_conditions": [], "test_commands": [CMD]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"db_path: {tmp_path / 'cli.db'}\nsafety:\n  approval_timeout_seconds: 5\n")
    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path / "ws")
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(BUG)
    (repo / "tests" / "test_calc.py").write_text(TEST)
    yield str(cfg), repo
    set_config(AppConfig())


def edit(search, replace):
    return {"edits": [{"path": "calc.py", "search": search, "replace": replace}], "create": [], "delete": [], "rationale": ""}


def lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_run_success_exits_zero_and_applies(env, capsys):
    cfg, repo = env
    ScriptedProvider.register("cli-ok", {"planner": [PLAN], "coder": [edit("a - b", "a + b")]})
    code = cli.main(["--config", cfg, "run", "--repo", str(repo), "--task", "Fix add() in calc.py",
                     "--provider", "scripted", "--model", "cli-ok", "--json"])
    out = lines(capsys)
    assert code == cli.EXIT_OK
    assert out[-1]["type"] == "summary" and out[-1]["outcome"] == "applied" and out[-1]["verdict"] == "passed"
    assert (repo / "calc.py").read_text().endswith("a + b\n")


def test_unapproved_failing_patch_exits_two_and_repo_is_untouched(env, capsys):
    cfg, repo = env
    ScriptedProvider.register("cli-bad", {"planner": [PLAN], "coder": [edit("a - b", "a * b")],
                                          "repair": [{"edits": [], "create": [], "delete": [], "rationale": ""}]})
    code = cli.main(["--config", cfg, "run", "--repo", str(repo), "--task", "Fix add() in calc.py",
                     "--provider", "scripted", "--model", "cli-bad", "--no-input", "--json"])
    out = lines(capsys)
    assert code == cli.EXIT_REJECTED and out[-1]["outcome"] == "rejected" and out[-1]["has_diff"]
    assert (repo / "calc.py").read_text() == BUG
    assert any(e["type"] == "approval_requested" for e in out)


def test_bad_repo_is_a_usage_error_with_a_hint(env, capsys):
    cfg, _ = env
    code = cli.main(["--config", cfg, "run", "--repo", "/etc", "--task", "x"])
    err = capsys.readouterr().err
    assert code == cli.EXIT_USAGE and "system location" in err and "hint" in err


def test_status_inspect_diff_roundtrip(env, capsys):
    cfg, repo = env
    ScriptedProvider.register("cli-rt", {"planner": [PLAN], "coder": [edit("a - b", "a + b")]})
    cli.main(["--config", cfg, "run", "--repo", str(repo), "--task", "Fix add() in calc.py",
              "--provider", "scripted", "--model", "cli-rt", "--json"])
    run_id = lines(capsys)[-1]["run_id"]

    assert cli.main(["--config", cfg, "status", "--json"]) == 0
    runs = json.loads(capsys.readouterr().out)
    assert runs[0]["id"] == run_id and runs[0]["outcome"] == "applied"

    assert cli.main(["--config", cfg, "inspect", run_id, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["events"][0]["type"] == "run_created"

    assert cli.main(["--config", cfg, "diff", run_id]) == 0
    assert "+    return a + b" in capsys.readouterr().out
    assert cli.main(["--config", cfg, "status", "does-not-exist"]) == cli.EXIT_USAGE


def test_doctor_has_no_failures_in_a_healthy_environment(env, capsys):
    cfg, _ = env
    assert cli.main(["--config", cfg, "doctor", "--json"]) == 0
    checks = json.loads(capsys.readouterr().out)
    assert {c["name"] for c in checks} >= {"python", "database", "sandbox-env", "patch-engine"}
    assert not [c for c in checks if c["status"] == FAIL]


def test_doctor_flags_unauthenticated_public_bind(env, monkeypatch):
    cfg, _ = env
    monkeypatch.delenv("PATCHQUEST_API_TOKEN", raising=False)
    cli._bootstrap(cfg)
    from patchquest.config import get_config
    get_config().host = "0.0.0.0"
    failing = [c for c in run_checks() if c.status == FAIL]
    assert any(c.name == "api-auth" and "PATCHQUEST_API_TOKEN" in c.fix for c in failing)


def test_providers_lists_configuration_state(env, capsys, monkeypatch):
    cfg, _ = env
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert cli.main(["--config", cfg, "providers", "--json"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["openai"]["configured"] is True and rows["mock"]["configured"] is True


def test_agent_that_produces_nothing_for_a_mutating_task_is_not_a_success(env, capsys):
    cfg, repo = env
    empty = {"edits": [], "create": [], "delete": [], "rationale": "", "tests_to_run": []}
    ScriptedProvider.register("cli-none", {"planner": [PLAN], "coder": [empty, empty]})
    code = cli.main(["--config", cfg, "run", "--repo", str(repo), "--task", "Fix add() in calc.py",
                     "--provider", "scripted", "--model", "cli-none", "--json"])
    out = lines(capsys)
    assert code == cli.EXIT_REJECTED and out[-1]["outcome"] == "no_patch"


def test_database_never_defaults_into_the_working_directory(tmp_path, monkeypatch):
    from patchquest.database import resolve_db_path

    monkeypatch.delenv("PATCHQUEST_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    assert resolve_db_path(None) == __import__("pathlib").Path.home() / ".patchquest" / "patchquest.db"
    (tmp_path / "patchquest.db").write_text("")  # a pre-existing legacy file keeps working
    assert resolve_db_path(None).name == "patchquest.db" and str(resolve_db_path(None)) == "patchquest.db"
    monkeypatch.setenv("PATCHQUEST_DB", str(tmp_path / "x.db"))
    assert resolve_db_path(None) == tmp_path / "x.db"

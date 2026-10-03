"""CLI end to end: exit codes, machine-readable output, approvals, doctor, providers, DB location.
"""

import json

import pytest

from patchquest import cli
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.config import AppConfig, set_config
from patchquest.doctor import FAIL, run_checks
from tests.support import CALC_BUG, CALC_TEST, PLAN, edit


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"db_path: {tmp_path / 'cli.db'}\nsafety:\n  approval_timeout_seconds: 5\n")
    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path / "ws")
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(CALC_BUG)
    (repo / "tests" / "test_calc.py").write_text(CALC_TEST)
    yield str(cfg), repo
    set_config(AppConfig())


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
    assert (repo / "calc.py").read_text() == CALC_BUG
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


class TestResumeCommands:
    """Crash a run in-process, then drive recovery purely through the CLI."""

    @pytest.fixture
    def crashed(self, env):
        import asyncio

        from tests.support import FIX, after_event, crash_run

        cfg, repo = env
        (repo / "README.md").write_text("# calc\n")
        review = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
                  "recommendation": "approve"}
        run_id = asyncio.run(crash_run(repo, {"planner": [PLAN], "coder": [FIX], "reviewer": [review]},
                                       after_event("checkpoint_created", phase="patching"),
                                       task="Fix add() in calc.py so it returns the sum"))
        return cfg, repo, run_id

    def test_plan_only_explains_and_changes_nothing(self, crashed, capsys):
        cfg, repo, run_id = crashed
        assert cli.main(["--config", cfg, "resume", run_id, "--plan"]) == cli.EXIT_OK
        err = capsys.readouterr().err
        for heading in ("LAST_CHECKPOINT", "INTERRUPTED_OPERATION", "REPO_DRIFT", "SIDE_EFFECT_CERTAINTY",
                        "RECOVERY_ACTION", "APPROVAL_REQUIRED"):
            assert heading in err
        assert (repo / "calc.py").read_text() == CALC_BUG
        assert cli.main(["--config", cfg, "status", run_id, "--json"]) == cli.EXIT_OK
        assert json.loads(capsys.readouterr().out)[0]["status"] == "interrupted"

    def test_resume_finishes_the_run_and_streams_only_new_events(self, crashed, capsys):
        cfg, repo, run_id = crashed
        assert cli.main(["--config", cfg, "resume", run_id, "--json"]) == cli.EXIT_OK
        out = lines(capsys)
        assert out[0]["type"] == "resume_plan" and out[0]["CATEGORY"] == "SAFE_RESUME"
        assert out[-1]["type"] == "summary" and out[-1]["outcome"] == "applied"
        streamed = [e for e in out if "id" in e]
        assert streamed and "phase_started" in {e["type"] for e in streamed}
        assert not any(e["type"] == "run_interrupted" for e in streamed)  # history is not replayed
        assert (repo / "calc.py").read_text().endswith("a + b\n")

    def test_drift_stops_with_a_distinct_exit_code_until_accepted(self, crashed, capsys):
        cfg, repo, run_id = crashed
        (repo / "calc.py").write_text("def add(a, b):\n    return b + a  # mine\n")
        assert cli.main(["--config", cfg, "resume", run_id]) == cli.EXIT_NEEDS_CONFIRMATION
        assert "--accept-drift" in capsys.readouterr().err
        assert "mine" in (repo / "calc.py").read_text()
        assert cli.main(["--config", cfg, "resume", run_id, "--accept-drift", "--json"]) == cli.EXIT_REJECTED
        assert "mine" in (repo / "calc.py").read_text()  # promotion refused to overwrite the human's file

    def test_finished_and_unknown_runs(self, env, crashed, capsys):
        cfg, _, run_id = crashed
        assert cli.main(["--config", cfg, "resume", run_id, "--json"]) == cli.EXIT_OK
        capsys.readouterr()
        assert cli.main(["--config", cfg, "resume", run_id]) == cli.EXIT_FAILED  # already completed
        assert cli.main(["--config", cfg, "resume", "no-such-run"]) == cli.EXIT_USAGE

    def test_checkpoints_and_events_are_inspectable(self, crashed, capsys):
        cfg, _, run_id = crashed
        assert cli.main(["--config", cfg, "checkpoints", run_id, "--json"]) == cli.EXIT_OK
        rows = json.loads(capsys.readouterr().out)
        assert [r["phase"] for r in rows][-1] == "patching" and {r["status"] for r in rows} == {"ok"}
        assert cli.main(["--config", cfg, "events", run_id, "--json"]) == cli.EXIT_OK
        events = lines(capsys)
        assert events[0]["type"] == "run_state_changed" and all(e["event_uid"] for e in events)
        cutoff = events[3]["id"]
        assert cli.main(["--config", cfg, "events", run_id, "--after", str(cutoff), "--json"]) == cli.EXIT_OK
        assert [e["id"] for e in lines(capsys)] == [e["id"] for e in events if e["id"] > cutoff]

    def test_status_shows_budget_consumption_even_for_an_interrupted_run(self, crashed, capsys):
        cfg, _, run_id = crashed
        assert cli.main(["--config", cfg, "status", run_id, "--json"]) == cli.EXIT_OK
        budget = {b["kind"]: b for b in json.loads(capsys.readouterr().out)[0]["budget"]}
        assert budget["model_calls"]["used"] >= 2 and budget["patch_attempts"]["used"] == 1
        assert cli.main(["--config", cfg, "status", run_id]) == cli.EXIT_OK
        assert "budget model_calls" in capsys.readouterr().out


class TestReplayForkCommands:
    REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
              "recommendation": "approve"}

    @pytest.fixture
    def finished(self, env, capsys):
        cfg, repo = env
        ScriptedProvider.register("orig", {"planner": [PLAN], "coder": [edit("a - b", "a + b")], "reviewer": [self.REVIEW]})
        assert cli.main(["--config", cfg, "run", "--repo", str(repo), "--task", "Fix add() in calc.py so it returns the sum",
                         "--provider", "scripted", "--model", "orig", "--json"]) == cli.EXIT_OK
        run_id = lines(capsys)[-1]["run_id"]
        (repo / "calc.py").write_text(CALC_BUG)  # put the bug back so replays/forks have something to (not) change
        return cfg, repo, run_id

    def test_state_replay_reports_a_consistent_history(self, finished, capsys):
        cfg, _, run_id = finished
        assert cli.main(["--config", cfg, "replay", run_id, "--json"]) == cli.EXIT_OK
        report = json.loads(capsys.readouterr().out)
        assert report["ok"] and report["status_trail"] == ["running", "completed"]

    def test_model_replay_matches_and_writes_nothing(self, finished, capsys):
        cfg, repo, run_id = finished
        assert cli.main(["--config", cfg, "replay", run_id, "--mode", "model", "--json"]) == cli.EXIT_OK
        out = lines(capsys)
        assert out[-1]["type"] == "comparison" and out[-1]["matched"] is True
        assert (repo / "calc.py").read_text() == CALC_BUG

    def test_fork_with_another_model_and_overrides_then_inspect_lineage(self, finished, capsys):
        cfg, repo, run_id = finished
        ScriptedProvider.register("alt", {"coder": [edit("a - b", "b + a")], "reviewer": [self.REVIEW]})
        assert cli.main(["--config", cfg, "fork", run_id, "--from", "6", "--model", "alt",
                         "--set", "agent.promote_policy=always", "--json"]) == cli.EXIT_OK
        summary = lines(capsys)[-1]
        assert summary["outcome"] == "applied" and (repo / "calc.py").read_text().endswith("b + a\n")
        assert cli.main(["--config", cfg, "lineage", summary["run_id"], "--json"]) == cli.EXIT_OK
        info = json.loads(capsys.readouterr().out)
        assert [r["id"] for r in info["ancestry"]] == [run_id, summary["run_id"]]
        assert info["ancestry"][1]["lineage_kind"] == "fork" and info["ancestry"][1]["parent_checkpoint_seq"] == 6

    def test_bad_overrides_and_unknown_runs_are_usage_errors(self, finished, capsys):
        cfg, _, run_id = finished
        assert cli.main(["--config", cfg, "fork", run_id, "--set", "safety.approval_timeout_seconds=1"]) == cli.EXIT_USAGE
        assert "cannot be overridden" in capsys.readouterr().err
        assert cli.main(["--config", cfg, "fork", run_id, "--set", "nonsense"]) == cli.EXIT_USAGE
        assert cli.main(["--config", cfg, "fork", "missing"]) == cli.EXIT_USAGE
        assert cli.main(["--config", cfg, "replay", "missing"]) == cli.EXIT_USAGE

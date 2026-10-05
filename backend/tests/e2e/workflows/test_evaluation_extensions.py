"""Attribution, recovery scenarios, experiments, the matrix and the regression gates."""

from __future__ import annotations

import json

import pytest

from patchquest import cli
from patchquest.evaluation import experiments, gates
from patchquest.evaluation.metrics import ATTRIBUTIONS, TaskResult, attribute
from patchquest.evaluation.recovery import SCENARIOS, run_recovery
from patchquest.evaluation.runner import run_eval


def result(**kw):
    base = {"id": "t", "category": "bug_fix", "difficulty": "easy", "status": "failure"}
    return TaskResult(**{**base, **kw})


class TestAttribution:
    @pytest.mark.parametrize("kw,scripted,expected", [
        ({"status": "success"}, False, None),
        ({"failure_kind": "MODEL_AUTH"}, False, "PROVIDER_FAILURE"),
        ({"failure_kind": "MODEL_UNAVAILABLE"}, False, "PROVIDER_FAILURE"),
        ({"failure_kind": "MODEL_INVALID_OUTPUT"}, False, "MODEL_FAILURE"),
        ({"failure_kind": "PATCH_APPLY"}, False, "MODEL_FAILURE"),
        ({"failure_kind": "BUDGET_EXHAUSTED"}, False, "BUDGET"),
        ({"failure_kind": "USER_CANCELLED"}, False, "TIMEOUT"),
        ({"failure_kind": "SANDBOX_FAILURE"}, False, "SANDBOX_FAILURE"),
        ({"failure_kind": "ENVIRONMENT_FAILURE"}, False, "ENVIRONMENT_FAILURE"),
        ({"failure_kind": "INTERNAL_INVARIANT"}, False, "HARNESS_FAILURE"),
        ({"failure_kind": "CHECKPOINT_FAILURE"}, False, "HARNESS_FAILURE"),
        ({"failure_reason": "declined"}, False, "MODEL_FAILURE"),
        ({"failure_reason": "validation_failed"}, False, "MODEL_FAILURE"),
        ({"failure_reason": "no_patch"}, False, "MODEL_FAILURE"),
        ({"failure_reason": "timeout"}, False, "TIMEOUT"),
        ({"failure_reason": "something nobody planned for"}, False, "HARNESS_FAILURE"),
        ({"oracle_returncode": 127, "failure_reason": "oracle_failed"}, False, "ORACLE_FAILURE"),
        ({"oracle_returncode": -1}, False, "ORACLE_FAILURE"),
        ({"oracle_returncode": 1, "failure_reason": "oracle_failed"}, False, "MODEL_FAILURE"),  # a failing hidden test is the model's
        ({"failure_reason": "declined"}, True, "HARNESS_FAILURE"),  # the scripted reference cannot be "wrong": it is ours
    ])
    def test_who_is_blamed(self, kw, scripted, expected):
        assert attribute(result(**kw), scripted=scripted) == expected

    def test_every_class_in_the_spec_is_reachable(self):
        reachable = {attribute(result(failure_kind=k), scripted=False) for k in
                     ("MODEL_AUTH", "MODEL_INVALID_OUTPUT", "BUDGET_EXHAUSTED", "USER_CANCELLED", "SANDBOX_FAILURE",
                      "ENVIRONMENT_FAILURE", "TOOL_FAILURE", "INTERNAL_INVARIANT")} | {attribute(result(oracle_returncode=127), scripted=False)}
        assert reachable == set(ATTRIBUTIONS)

    @pytest.mark.asyncio
    async def test_a_scripted_run_that_fails_is_the_harnesss_fault_and_reports_it(self):
        report = (await run_eval(provider="scripted", scripted_mode="null", only="bugfix-leap-year")).to_dict()
        task = report["tasks"][0]
        assert task["status"] != "success" and task["attribution"] == "HARNESS_FAILURE"
        assert report["summary"]["attribution"] == {"HARNESS_FAILURE": 1}


class TestSignTest:
    @pytest.mark.parametrize("wins,losses,p", [(0, 0, None), (5, 0, 0.0625), (0, 5, 0.0625), (3, 3, 1.0), (8, 2, 0.1094),
                                               (10, 0, 0.002), (1, 0, 1.0), (7, 3, 0.3438)])
    def test_exact_two_sided_values(self, wins, losses, p):
        assert experiments.sign_test_p(wins, losses) == p

    def test_targets_parse(self):
        t = experiments.parse_target("sglang:Qwen/Qwen3-0.6B@http://localhost:30000/v1")
        assert (t.provider, t.model, t.base_url, t.label) == ("sglang", "Qwen/Qwen3-0.6B", "http://localhost:30000/v1", "sglang:Qwen/Qwen3-0.6B")
        assert experiments.parse_target("scripted").model is None
        with pytest.raises(ValueError):
            experiments.parse_target(":model")


@pytest.fixture(scope="module")
def report():
    import asyncio

    return asyncio.run(run_recovery())


class TestRecovery:
    def test_every_scenario_ends_correct_and_safe(self, report):
        failed = {s["id"]: s["failures"] for s in report["scenarios"] if not s["passed"]}
        assert failed == {} and report["summary"]["pass_rate"] == 1.0
        assert {s["id"] for s in report["scenarios"]} == {sc.id for sc in SCENARIOS}
        assert report["baseline"]["id"] == "uninterrupted" and report["baseline"]["passed"]

    def test_the_interesting_judgements_are_the_right_ones(self, report):
        by = {s["id"]: s for s in report["scenarios"]}
        assert by["human-edits-the-same-file"]["category"] == "HUMAN_CONFIRMATION_REQUIRED"
        assert by["human-edits-the-same-file"]["outcome"] == "conflict"  # their edit survived; the patch was refused
        assert by["crash-before-any-checkpoint"]["category"] == "SAFE_RETRY"
        assert by["cancelled-midway"]["status"] == "cancelled"
        clean = report["baseline"]["model_calls"]
        assert by["crash-after-patching"]["model_calls"] == clean  # nothing completed was repeated
        assert by["damaged-newest-checkpoint"]["model_calls"] == clean + 1  # one phase re-ran from the older checkpoint

    @pytest.mark.asyncio
    async def test_the_scenarios_can_fail(self, monkeypatch):
        """A suite that cannot fail proves nothing: blind the drift check and the human-edit scenario must catch it."""
        from patchquest.runtime import fingerprint

        monkeypatch.setattr(fingerprint, "classify", lambda *a, **k: fingerprint.DriftReport(fingerprint.Drift.NO_DRIFT))
        result = await run_recovery(only="human-edits")
        assert result["summary"] == {"scenarios": 1, "passed": 0, "pass_rate": 0.0}
        assert any("resume plan said" in f for f in result["scenarios"][0]["failures"])


class TestExperimentsAndMatrix:
    @pytest.mark.asyncio
    async def test_a_paired_experiment_finds_the_winner_and_says_how_sure_it_is(self):
        data = await experiments.run_experiment(baseline={"agent.promote_policy": "never"}, candidate={"agent.promote_policy": "on_green"},
                                                target=experiments.Target("scripted"), only="bugfix-leap-year")
        wins = data["paired"]["candidate_wins"]
        assert wins and data["paired"]["candidate_losses"] == [] and data["baseline"]["success"] == 0
        assert data["candidate"]["success"] == len(wins) == data["tasks"]
        assert data["paired"]["sign_test_p"] == experiments.sign_test_p(len(wins), 0)
        assert data["baseline_overrides"] == {"agent.promote_policy": "never"}
        assert "too few" in data["reading"].lower() or "sign-test" in data["reading"].lower()

    @pytest.mark.asyncio
    async def test_experiments_refuse_nonsense(self):
        with pytest.raises(ValueError, match="identical"):
            await experiments.run_experiment(baseline={"agent.max_retries": 3}, candidate={"agent.max_retries": 3}, target=experiments.Target("scripted"))
        with pytest.raises(ValueError, match="cannot be overridden"):
            await experiments.run_experiment(baseline={}, candidate={"safety.approval_timeout_seconds": 1}, target=experiments.Target("scripted"))

    @pytest.mark.asyncio
    async def test_the_matrix_has_one_row_per_target_over_the_same_corpus(self):
        data = await experiments.run_matrix([experiments.Target("scripted"), experiments.Target("scripted")], only="bugfix-leap-year")
        assert len(data["table"]) == 2 and data["corpus_digest"] and all(r["success"] == r["tasks"] == 1 for r in data["table"])
        assert {"tokens", "model_calls", "wall_s", "attribution", "approvals_requested"} <= set(data["table"][0])


class TestGates:
    @pytest.mark.asyncio
    async def test_tier0_proves_the_harness_and_the_oracle_are_not_vacuous(self):
        t = await gates.tier0()
        assert t.passed and t.details["reference_success"] == t.details["tasks"] and t.details["null_success"] == 0

    @pytest.mark.asyncio
    async def test_tier0_fails_if_a_null_solution_passes(self, monkeypatch):
        from patchquest.evaluation import runner

        real = runner._script_for
        monkeypatch.setattr(runner, "_script_for", lambda task, mode="reference": real(task, mode="reference"))  # null == reference
        t = await gates.tier0()
        assert not t.passed and any("oracle proves nothing" in p for p in t.problems)

    @pytest.mark.asyncio
    async def test_tier2_replays_every_corpus_run_identically(self):
        t = await gates.tier2()
        assert t.passed, t.problems
        assert t.details["replayed"] == 14

    @pytest.mark.asyncio
    async def test_live_tiers_without_a_provider_are_skipped_never_passed(self):
        gate = await gates.run_gate(3)
        live = next(t for t in gate["tiers"] if t["tier"] == 3)
        assert live["skipped"] and not live["passed"] and gate["passed"] is False
        assert [t["tier"] for t in gate["tiers"]] == [0, 1, 2, 3] and gate["highest_passed"] == 2

    @pytest.mark.asyncio
    async def test_a_failed_cheap_tier_stops_the_expensive_ones(self, monkeypatch):
        async def broken():
            return gates.TierResult(0, "harness", False, problems=["broken"])

        monkeypatch.setattr(gates, "tier0", broken)
        gate = await gates.run_gate(2)
        assert [t["tier"] for t in gate["tiers"]] == [0] and gate["passed"] is False

    @pytest.mark.asyncio
    async def test_tier_numbers_are_validated(self):
        with pytest.raises(ValueError):
            await gates.run_gate(7)

    @pytest.mark.asyncio
    async def test_tier3_blames_the_harness_not_the_model(self, monkeypatch):
        """A provider/harness failure in the live smoke tier fails the gate; the model merely being wrong does not."""
        async def fake_eval(**kw):
            class R:
                def to_dict(self_inner):
                    kind = "MODEL_UNAVAILABLE" if kw["only"] == "feature-slugify" else None
                    status = "failure"
                    return {"tasks": [{"status": status, "attribution": "PROVIDER_FAILURE" if kind else "MODEL_FAILURE",
                                       "failure_kind": kind, "failure_reason": "declined"}], "summary": {}}
            return R()

        monkeypatch.setattr(gates, "run_eval", fake_eval)
        t = await gates.tier3("sglang", "m", None, 10)
        assert not t.passed and len(t.problems) == 1 and "feature-slugify: PROVIDER_FAILURE" in t.problems[0]
        assert t.details["attribution"] == {"MODEL_FAILURE": 2, "PROVIDER_FAILURE": 1}


class TestCli:
    def test_recovery_and_gate_and_experiment(self, capsys, tmp_path):
        assert cli.main(["eval", "recovery", "--filter", "crash-after-promotion", "--json"]) == cli.EXIT_OK
        assert json.loads(capsys.readouterr().out)["summary"]["pass_rate"] == 1.0
        assert cli.main(["eval", "gate", "--tier", "0", "--json"]) == cli.EXIT_OK
        assert json.loads(capsys.readouterr().out)["highest_passed"] == 0
        assert cli.main(["eval", "gate", "--tier", "3"]) == cli.EXIT_FAILED  # no provider: skipped, so not green
        out = tmp_path / "exp.json"
        assert cli.main(["eval", "experiment", "--filter", "bugfix-leap-year", "--baseline", "agent.promote_policy=never",
                         "--candidate", "agent.promote_policy=on_green", "--out", str(out)]) == cli.EXIT_OK
        assert json.loads(out.read_text())["paired"]["candidate_wins"] == ["bugfix-leap-year"]
        assert cli.main(["eval", "experiment", "--baseline", "safety.x=1", "--candidate", "agent.max_retries=1"]) == cli.EXIT_USAGE
        assert cli.main(["eval", "matrix", "--target", "scripted", "--target", "scripted", "--filter", "bugfix-leap-year", "--json"]) == cli.EXIT_OK
        assert len(json.loads(capsys.readouterr().out.splitlines()[-1])["table"]) == 2

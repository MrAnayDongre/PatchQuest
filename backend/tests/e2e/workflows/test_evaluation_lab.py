"""Evaluation lab: corpus validity, scoring correctness (negative controls), comparison and the eval CLI.

Every reference solution must pass (engine regression gate), and the oracle must be able to say no: overfitting the
visible test is 'partial', touching a forbidden file is flagged, doing nothing or a broken fix fails.
"""

import dataclasses
import json
import shutil

import pytest

from patchquest import cli
from patchquest.application.service import TaskService
from patchquest.config import AppConfig, set_config
from patchquest.evaluation import compare, corpus_digest, load_corpus, run_eval
from patchquest.evaluation.metrics import TaskResult, classify, summarize
from patchquest.evaluation.runner import run_task
from patchquest.evaluation.tasks import SCHEMA_VERSION


@pytest.fixture(autouse=True)
def unattended_approvals():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    set_config(cfg)



needs_node = pytest.mark.skipif(not (shutil.which("npm") and shutil.which("node")), reason="needs node and npm")


def one(task_id):
    (task,) = load_corpus(only=task_id)
    return task


async def score(task, tmp_path):
    svc = TaskService()
    return await run_task(task, svc, tmp_path / "repos", provider="scripted", model=None, base_url=None, time_limit=60)


class TestCorpus:
    def test_bundled_corpus_is_valid_and_diverse(self):
        tasks = load_corpus()
        assert len(tasks) >= 14 and len({t.id for t in tasks}) == len(tasks)
        assert {"bug_fix", "feature", "refactor", "test_repair", "multi_file", "security_fix", "api_change",
                "cli_change", "backend", "frontend"} <= {t.category for t in tasks}
        for t in tasks:
            assert t.files and t.oracle_files and t.solution["edits"], t.id  # every task has a hidden oracle and a reference

    def test_filters(self):
        assert {t.category for t in load_corpus(only="security_fix")} == {"security_fix"}
        assert [t.id for t in load_corpus(only="feature-slugify,bugfix-leap-year")] == ["bugfix-leap-year", "feature-slugify"]
        with pytest.raises(ValueError):
            load_corpus(only="nope")

    def test_schema_and_duplicates_are_rejected(self, tmp_path):
        (tmp_path / "a.yaml").write_text("schema: 99\nid: a\ncategory: c\ntask: t\nfiles: {}\ntest_command: x\noracle: {command: x}\n")
        with pytest.raises(ValueError, match="schema"):
            load_corpus(tmp_path)
        assert SCHEMA_VERSION == 1

    def test_digest_is_stable_and_content_sensitive(self, tmp_path):
        d1 = corpus_digest()
        assert d1 == corpus_digest()
        (tmp_path / "t.yaml").write_text("x")
        assert corpus_digest(tmp_path) != d1


class TestEveryReferenceSolutionPasses:
    """Engine regression gate: if this fails, either the engine or a corpus task broke."""

    @pytest.mark.asyncio
    async def test_python_tasks(self, tmp_path):
        report = await run_eval(only="bug_fix,feature,refactor,test_repair,multi_file,security_fix,api_change,cli_change,backend")
        bad = [(t["id"], t["failure_reason"]) for t in report.tasks if t["status"] != "success"
               and not t["id"].startswith("js-")]
        assert not bad, bad

    @needs_node
    @pytest.mark.asyncio
    async def test_js_tasks(self):
        report = await run_eval(only="js-bugfix-empty-average,js-feature-capitalize")
        assert [t["status"] for t in report.tasks] == ["success", "success"]


class TestScoringIsNotGameable:
    """Negative controls: the oracle must be able to say no."""

    @pytest.mark.asyncio
    async def test_overfitting_to_the_visible_test_is_partial_not_success(self, tmp_path):
        task = one("bugfix-pagination-off-by-one")
        hack = {"files_to_inspect": ["pager.py"], "create": [], "edits": [
            {"path": "pager.py", "search": "    start = page * size\n    return items[start:start + size]",
             "replace": "    if page == 1:\n        return items[:size]\n    start = page * size\n    return items[start:start + size]"}]}
        r = await score(dataclasses.replace(task, solution=hack), tmp_path)
        assert r.verdict == "passed" and r.outcome == "applied"  # it satisfied what the agent could see...
        assert (r.status, r.failure_reason) == ("partial", "oracle_failed")  # ...but not the hidden oracle

    @pytest.mark.asyncio
    async def test_touching_a_forbidden_file_is_flagged(self, tmp_path):
        task = one("testrepair-api-changed")
        cheat = dict(task.solution)
        cheat["edits"] = [{"path": "connection.py", "search": "    host, _, port = address.partition(\":\")\n    return host, int(port)",
                           "replace": "    host, _, port = address.partition(\":\")\n    return host, int(port)\n\n\n_x = 1"}, *task.solution["edits"]]
        r = await score(dataclasses.replace(task, solution=cheat), tmp_path)
        assert r.forbidden_change and (r.status, r.failure_reason) == ("partial", "forbidden_change")

    @pytest.mark.asyncio
    async def test_agent_that_does_nothing_fails(self, tmp_path):
        task = one("bugfix-leap-year")
        r = await score(dataclasses.replace(task, solution={"files_to_inspect": [], "edits": [], "create": []}), tmp_path)
        assert r.status == "failure" and r.failure_reason == "no_patch" and not r.oracle_passed

    @pytest.mark.asyncio
    async def test_fix_that_passes_the_visible_test_but_is_wrong_is_partial(self, tmp_path):
        task = one("bugfix-leap-year")
        wrong = {"files_to_inspect": ["calendar_utils.py"], "create": [], "edits": [
            {"path": "calendar_utils.py", "search": "    return year % 4 == 0 or year % 400 == 0", "replace": "    return year % 400 == 0"}]}
        r = await score(dataclasses.replace(task, solution=wrong), tmp_path)
        assert (r.status, r.failure_reason) == ("partial", "oracle_failed") and not r.oracle_passed

    @pytest.mark.asyncio
    async def test_fix_that_fails_even_the_visible_test_is_a_failure_and_is_not_applied(self, tmp_path):
        task = one("bugfix-leap-year")
        broken = {"files_to_inspect": ["calendar_utils.py"], "create": [], "edits": [
            {"path": "calendar_utils.py", "search": "    return year % 4 == 0 or year % 400 == 0", "replace": "    return True"}]}
        r = await score(dataclasses.replace(task, solution=broken), tmp_path)
        assert r.status == "failure" and r.failure_reason == "validation_failed" and r.outcome == "rejected"
        assert r.repair_rounds == 1 and not r.oracle_passed  # an empty repair answer ends the loop


class TestMeasurementPlumbing:
    @pytest.mark.asyncio
    async def test_metrics_come_from_recorded_calls_not_guesses(self, tmp_path):
        r = await score(one("bugfix-leap-year"), tmp_path)
        assert r.status == "success" and r.model_calls >= 3 and r.files_touched == 1
        assert r.lines_added >= 1 and r.lines_removed >= 1 and r.wall_s > 0 and r.run_id

    @pytest.mark.asyncio
    async def test_eval_never_touches_the_callers_database(self, tmp_path):
        from patchquest.database import get_db, get_db_path

        before = get_db_path()
        await run_eval(only="bugfix-leap-year")
        assert get_db_path() == before
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    def test_failure_taxonomy(self):
        def res(**kw):
            return TaskResult(id="x", category="c", difficulty="d", **kw)
        assert classify(res(run_status="completed", outcome="applied", oracle_passed=True)) == ("success", None)
        assert classify(res(run_status="completed", outcome="applied")) == ("partial", "oracle_failed")
        assert classify(res(run_status="completed", outcome="no_patch")) == ("failure", "no_patch")
        assert classify(res(run_status="completed", outcome="rejected", verdict="regression")) == ("failure", "validation_failed")
        assert classify(res(run_status="failed", error="model-call budget exhausted")) == ("failure", "budget_exceeded")
        assert classify(res(run_status="failed", error="LLM provider 'x' call failed")) == ("failure", "provider_error")
        assert classify(res(run_status="completed", outcome="conflict")) == ("failure", "conflict")

    def test_summary_aggregates_by_category(self):
        rs = [TaskResult("a", "bug_fix", "easy", status="success"), TaskResult("b", "bug_fix", "easy", status="failure",
              failure_reason="no_patch"), TaskResult("c", "feature", "easy", status="partial", failure_reason="oracle_failed")]
        s = summarize(rs)
        assert s["overall"]["success_rate"] == pytest.approx(1 / 3, abs=1e-3)
        assert s["by_category"]["bug_fix"]["success"] == 1 and s["failure_reasons"] == {"no_patch": 1, "oracle_failed": 1}


class TestCompare:
    def make(self, statuses, digest="d1"):
        tasks = [{"id": k, "status": v, "failure_reason": None if v == "success" else "x"} for k, v in statuses.items()]
        ok = sum(1 for v in statuses.values() if v == "success")
        return {"corpus_digest": digest, "tasks": tasks,
                "summary": {"overall": {"success_rate": ok / len(statuses)}, "totals": {"tokens": 1}}}

    def test_detects_regressions_and_fixes(self):
        d = compare(self.make({"a": "success", "b": "failure", "c": "success"}),
                    self.make({"a": "failure", "b": "success", "c": "success"}))
        assert d["regressions"] == ["a"] and d["fixes"] == ["b"] and d["success_rate"]["delta"] == 0

    def test_refuses_different_corpora(self):
        with pytest.raises(ValueError, match="different corpora"):
            compare(self.make({"a": "success"}, "d1"), self.make({"a": "success"}, "d2"))


class TestEvalCli:
    def test_run_writes_results_and_gates_on_success_rate(self, tmp_path, capsys):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(f"db_path: {tmp_path / 'cli.db'}\n")
        out = tmp_path / "res.json"
        code = cli.main(["--config", str(cfg), "eval", "run", "--filter", "bugfix-leap-year,feature-slugify",
                         "--out", str(out), "--fail-under", "1.0"])
        data = json.loads(out.read_text())
        assert code == 0 and data["summary"]["overall"]["success"] == 2 and data["result_schema"] == 1
        assert data["environment"]["provider"] == "scripted" and data["corpus_digest"] == corpus_digest()
        assert cli.main(["--config", str(cfg), "eval", "compare", str(out), str(out)]) == 0

    def test_list_and_bad_filter(self, tmp_path, capsys):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(f"db_path: {tmp_path / 'cli.db'}\n")
        assert cli.main(["--config", str(cfg), "eval", "list", "--json"]) == 0
        assert len(json.loads(capsys.readouterr().out)) >= 14
        assert cli.main(["--config", str(cfg), "eval", "run", "--filter", "nonexistent"]) == cli.EXIT_USAGE

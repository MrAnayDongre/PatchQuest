"""Metrics are computed from real runs; every number below can be derived by hand from the scenario."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.observability.metrics import MetricsQuery, compute, parse_window, percentile
from tests.support import (
    FIX,
    PLAN,
    WRONG,
    after_event,
    crash_run,
    make_calc_repo,
    resume_run,
    run_scripted,
)

REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}
REPAIR = {"edits": [{"path": "calc.py", "search": "return a * b", "replace": "return a + b"}], "create": [], "delete": [], "rationale": ""}


class TestPrimitives:
    @pytest.mark.parametrize("values,p,expected", [
        ([], 50, None), ([5], 50, 5), ([5], 95, 5), ([1, 2, 3, 4], 50, 2), ([1, 2, 3, 4], 95, 4),
        ([4, 1, 3, 2], 50, 2), (list(range(1, 101)), 95, 95), (list(range(1, 101)), 50, 50), ([1, 2], 0, 1)])
    def test_nearest_rank_percentiles(self, values, p, expected):
        assert percentile(values, p) == expected

    def test_windows(self):
        now = datetime(2026, 10, 2, 12, tzinfo=UTC)
        assert parse_window("24h", now) == (now - timedelta(hours=24)).isoformat()
        assert parse_window("7d", now) == (now - timedelta(days=7)).isoformat()
        assert parse_window("30m", now) == (now - timedelta(minutes=30)).isoformat()
        for bad in ("", "d", "7", "x7d", "7y", "-3d", "1.5d"):
            with pytest.raises(ValueError):
                parse_window(bad)

    def test_unknown_grouping_is_an_error(self):
        with get_db() as conn, pytest.raises(ValueError, match="cannot group by"):
            compute(conn, MetricsQuery(group_by="colour"))


@pytest.fixture
async def history(tmp_path):
    """Five runs: clean success, repaired success, model failure, crashed-then-resumed success, and one with an approval."""
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    set_config(cfg)
    ids = {}
    _, ids["clean"] = await run_scripted(make_calc_repo(tmp_path / "r1"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    _, ids["repaired"] = await run_scripted(make_calc_repo(tmp_path / "r2"),
                                            {"planner": [PLAN], "coder": [WRONG], "repair": [REPAIR], "reviewer": [REVIEW]})
    _, ids["failed"] = await run_scripted(make_calc_repo(tmp_path / "r3"), {"planner": [PLAN], "coder": []})
    ids["resumed"] = await crash_run(make_calc_repo(tmp_path / "r4"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]},
                                     after_event("checkpoint_created", phase="patching"))
    await resume_run(ids["resumed"])
    _, ids["approved"] = await run_scripted(make_calc_repo(tmp_path / "r5"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    with get_db() as conn:  # a human decision five seconds after the request, on the last run
        conn.execute("INSERT INTO approvals (id, run_id, type, status, created_at, resolved_at) VALUES "
                     "('ap1', ?, 'command', 'approved', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:05+00:00')", (ids["approved"],))
        conn.execute("INSERT INTO run_events (run_id, type, created_at, event_uid) VALUES (?, 'approval_decided', 'n', 'u-ap1')",
                     (ids["approved"],))
    return ids


@pytest.mark.asyncio
async def test_totals_match_the_scenario_exactly(history):
    with get_db() as conn:
        t = compute(conn, MetricsQuery())["totals"]
    assert (t["runs"], t["finished"]) == (5, 5)
    assert t["by_status"] == {"completed": 4, "failed": 1}
    assert t["by_outcome"] == {"applied": 4, "rejected": 1}
    assert t["task_success_rate"] == 0.8  # 4 of 5
    assert t["validation_pass_rate"] == 1.0  # the four runs that reached a verdict all passed
    assert t["first_pass_success_rate"] == 0.4  # clean + approved; not the repaired one, not the resumed one, not the failure
    assert t["resume_success_rate"] == 1.0 and t["runs_resumed"] == 1
    assert t["failure_distribution"] == {"INTERNAL_INVARIANT": 1}  # the exhausted script is an unrecognised error
    assert t["mean_repair_rounds"] == 0.2  # one repair round across five finished runs
    assert t["human_interventions"] == 1 and t["approval_latency_s"] == {"p50": 5.0, "p95": 5.0, "count": 1}


@pytest.mark.asyncio
async def test_tokens_calls_and_time_are_reported_without_inventing_cost(history):
    with get_db() as conn:
        t = compute(conn, MetricsQuery())["totals"]
    assert t["mean_model_calls"] and t["time_to_completion_s"]["p50"] is not None
    assert t["time_to_first_model_call_s"]["p50"] is not None
    assert t["cost"] == {"total": None, "per_success": None, "currency": None}  # nothing priced: no dollar figure
    assert t["tokens"] == {"total": 0, "prompt": 0, "completion": 0, "per_success": 0.0}  # the scripted model reports no usage
    assert t["model_compute_s"] >= 0


@pytest.mark.asyncio
async def test_cost_appears_only_when_every_model_in_the_window_is_priced(history):
    with get_db() as conn:
        models = [r[0] for r in conn.execute("SELECT DISTINCT model FROM runs WHERE model IS NOT NULL")]
        conn.execute("UPDATE model_calls SET prompt_tokens = 1000000, completion_tokens = 500000")
        partial = compute(conn, MetricsQuery(pricing={models[0]: {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}))["totals"]
        full = compute(conn, MetricsQuery(pricing={m: {"input_per_mtok": 1.0, "output_per_mtok": 2.0} for m in models}))["totals"]
    assert partial["cost"]["total"] is None  # one priced model out of five is not "the cost"
    calls = full["mean_model_calls"] * 5
    assert full["cost"]["total"] == pytest.approx(calls * 2.0) and full["cost"]["currency"] == "USD"
    assert full["cost"]["per_success"] == pytest.approx(full["cost"]["total"] / 4)


@pytest.mark.asyncio
async def test_grouping_and_model_latency(history):
    with get_db() as conn:
        by_provider = compute(conn, MetricsQuery(group_by="provider"))
        by_repo = compute(conn, MetricsQuery(group_by="repository"))
    assert set(by_provider["groups"]) == {"scripted"} and by_provider["groups"]["scripted"]["runs"] == 5
    assert len(by_repo["groups"]) == 5 and all(g["runs"] == 1 for g in by_repo["groups"].values())
    row = by_provider["models"][0]
    assert row["provider"] == "scripted" and row["calls"] > 0 and row["latency_ms"]["p50"] is not None


@pytest.mark.asyncio
async def test_window_and_workspace_scoping(history):
    with get_db() as conn:
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        assert compute(conn, MetricsQuery(since=future))["totals"]["runs"] == 0
        assert compute(conn, MetricsQuery(until=future))["totals"]["runs"] == 5
        assert compute(conn, MetricsQuery(workspace_ids=["ws_somewhere_else"]))["totals"]["runs"] == 0
        assert compute(conn, MetricsQuery(workspace_ids=[]))["totals"]["runs"] == 0
        empty = compute(conn, MetricsQuery(workspace_ids=[]))["totals"]
    assert empty["task_success_rate"] is None and empty["time_to_completion_s"]["p50"] is None  # no data is null, not 0


def test_no_runs_at_all_reports_nulls_not_zeros():
    with get_db() as conn:
        t = compute(conn, MetricsQuery())["totals"]
    assert t["runs"] == 0 and t["task_success_rate"] is None and t["mean_model_calls"] is None


@pytest.mark.asyncio
async def test_api_is_scoped_to_the_callers_workspaces(tmp_path):
    from patchquest.application import TaskService
    from patchquest.domain.identity import Role
    from patchquest.main import app
    from patchquest.persistence import identity as ids

    with get_db() as conn:
        org = ids.create_org(conn, "Acme")
        a, b = ids.create_workspace(conn, org, "a"), ids.create_workspace(conn, org, "b")
        p = ids.create_principal(conn, org, "ana")
        ids.set_role(conn, p, a, Role.VIEWER)
        _, token = ids.issue_token(conn, p)
    repo = make_calc_repo(tmp_path / "r")
    svc = TaskService()
    svc.create_run(repo_path=str(repo), task="t", workspace_id=a)
    svc.create_run(repo_path=str(repo), task="t", workspace_id=b)
    svc.create_run(repo_path=str(repo), task="t", workspace_id=b)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                 headers={"Authorization": f"Bearer {token}"}) as c:
        body = (await c.get("/api/metrics?window=1d")).json()
        assert body["totals"]["runs"] == 1  # workspace b's two runs are invisible
        assert (await c.get("/api/metrics?window=nonsense")).status_code == 422
        assert (await c.get("/api/metrics?group_by=colour")).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        assert (await c.get("/api/metrics")).status_code == 401


@pytest.mark.asyncio
async def test_cli_prints_and_emits_json(history, capsys):
    from patchquest import cli

    assert cli.main(["metrics", "--window", "1d", "--json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["totals"]["runs"] == 5
    assert cli.main(["metrics", "--window", "1d", "--by", "provider"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "5 runs (5 finished)" in out and "success 80%" in out and "provider scripted" in out
    assert cli.main(["metrics", "--window", "soon"]) == cli.EXIT_USAGE

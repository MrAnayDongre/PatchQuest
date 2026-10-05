"""The OTLP trace is a faithful view of the ledger: ids, nesting, timing, failures, crash and resume."""

from __future__ import annotations

import json
import re

import pytest

from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.observability.trace import build_trace
from tests.support import FIX, PLAN, after_event, crash_run, make_calc_repo, resume_run, run_scripted

REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}


@pytest.fixture(autouse=True)
def unattended():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    set_config(cfg)


def spans_of(run_id):
    with get_db() as conn:
        trace = build_trace(conn, run_id)
    return trace, trace["resourceSpans"][0]["scopeSpans"][0]["spans"]


def attrs(span):
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


@pytest.mark.asyncio
async def test_a_completed_run_is_a_well_formed_tree(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    trace, spans = spans_of(rid)
    root = next(s for s in spans if s["name"] == "patchquest.run")
    assert re.fullmatch(r"[0-9a-f]{32}", root["traceId"]) and re.fullmatch(r"[0-9a-f]{16}", root["spanId"])
    assert {s["traceId"] for s in spans} == {root["traceId"]}
    by_id = {s["spanId"]: s for s in spans}
    assert len(by_id) == len(spans)  # span ids are unique
    for s in spans:
        if s is not root:
            assert s["parentSpanId"] in by_id  # no orphans
        assert int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"])
    phases = [s for s in spans if s["name"].startswith("phase ")]
    assert len(phases) == 12 and all(s["parentSpanId"] == root["spanId"] for s in phases)
    assert attrs(root)["patchquest.status"] == "completed" and attrs(root)["patchquest.outcome"] == "applied"
    resource = trace["resourceSpans"][0]["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "patchquest"}} in resource


@pytest.mark.asyncio
async def test_model_calls_and_commands_nest_under_their_phase(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    _, spans = spans_of(rid)
    by_id = {s["spanId"]: s for s in spans}
    models = [s for s in spans if s["name"].startswith("model ")]
    commands = [s for s in spans if s["name"] == "command"]
    assert {s["name"] for s in models} >= {"model planner", "model coder", "model reviewer"}
    planner = next(s for s in models if s["name"] == "model planner")
    assert by_id[planner["parentSpanId"]]["name"] == "phase planning"
    assert commands and all(by_id[c["parentSpanId"]]["name"] in ("phase testing", "phase static_checks") for c in commands)
    assert attrs(planner)["gen_ai.request.model"].startswith("script-") and attrs(planner)["patchquest.role"] == "planner"


@pytest.mark.asyncio
async def test_a_failed_run_marks_the_failing_phase_and_the_root(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": []})
    _, spans = spans_of(rid)
    root = next(s for s in spans if s["name"] == "patchquest.run")
    failed = [s for s in spans if s["status"]["code"] == 2 and s["name"] == "phase patching"]
    assert root["status"]["code"] == 2 and failed and attrs(failed[0])["patchquest.result"] == "failed"


@pytest.mark.asyncio
async def test_a_crash_leaves_an_unfinished_span_and_resume_adds_the_second_attempt(tmp_path):
    rid = await crash_run(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]},
                          after_event("command_started"))
    _, crashed = spans_of(rid)
    unfinished = [s for s in crashed if attrs(s).get("patchquest.result") == "unfinished"]
    assert unfinished and unfinished[0]["status"]["message"] == "did not finish"
    await resume_run(rid)
    _, final = spans_of(rid)
    attempts = {attrs(s)["patchquest.attempt"] for s in final if s["name"].startswith("phase ")}
    assert attempts == {"1", "2"}  # intValue is serialised as a string in OTLP/JSON


@pytest.mark.asyncio
async def test_export_is_deterministic(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    first, second = spans_of(rid)[0], spans_of(rid)[0]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_unknown_run():
    with get_db() as conn, pytest.raises(LookupError):
        build_trace(conn, "nope")


@pytest.mark.asyncio
async def test_cli_prints_json_and_posts_to_a_collector(tmp_path, capsys, monkeypatch):
    import httpx

    from patchquest import cli

    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    assert cli.main(["trace", rid]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["resourceSpans"]
    posted = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: posted.append((url, json)) or httpx.Response(
        200, request=httpx.Request("POST", url)))
    assert cli.main(["trace", rid, "--endpoint", "http://collector:4318/v1/traces"]) == cli.EXIT_OK
    assert posted[0][0] == "http://collector:4318/v1/traces" and posted[0][1]["resourceSpans"]
    assert cli.main(["trace", "missing"]) == cli.EXIT_USAGE

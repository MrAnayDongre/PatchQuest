#!/usr/bin/env python3
"""Evidence checks for the demo qualification, against a running `patchquest demo` (stdlib only).

    demo_qualify_evidence.py <base-url> <demo-dir> seed|transcript|replay|fork|metrics

Each check prints what it measured as JSON and exits non-zero if an expectation fails.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path


def call(base: str, path: str, body: dict | None = None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def wait_done(base: str, run_id: str, limit: float = 90.0) -> dict:
    end = time.time() + limit
    while time.time() < end:
        run = call(base, f"/api/runs/{run_id}")
        if run["status"] in ("completed", "failed", "cancelled", "interrupted"):
            return run
        time.sleep(0.5)
    raise SystemExit(f"run {run_id} did not finish in {limit}s")


def expect(ok: bool, what: str, facts: dict) -> None:
    print(json.dumps({"check": what, "ok": ok, **facts}, default=str))
    if not ok:
        raise SystemExit(f"FAILED: {what}")


def events_count(demo: Path, run_id: str) -> int:
    with sqlite3.connect(f"file:{demo / 'patchquest.db'}?mode=ro", uri=True) as c:
        return c.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run_id,)).fetchone()[0]


def model_calls(demo: Path, run_id: str) -> list[tuple[str, str]]:
    with sqlite3.connect(f"file:{demo / 'patchquest.db'}?mode=ro", uri=True) as c:
        return c.execute("SELECT provider, status FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()


def run_by_task(base: str, needle: str) -> dict:
    runs = call(base, "/api/runs")
    found = [r for r in runs if needle in r["task"]]
    if not found:
        raise SystemExit(f"no run mentioning {needle!r}")
    return found[-1]


def seed(base: str, demo: Path) -> None:
    runs = call(base, "/api/runs")
    by = {}
    for r in runs:
        by[r["status"]] = by.get(r["status"], 0) + 1
    wf = call(base, "/api/workflows")
    ints = call(base, f"/api/integrations?workspace_id=ws_local")
    expect(len(runs) >= 6 and len(wf) >= 1, "seeded environment", {"runs": len(runs), "by_status": by, "workflows": [w["name"] for w in wf],
                                                                 "integrations": [i["kind"] for i in ints]})


def transcript(base: str, demo: Path) -> None:
    t = call(base, "/api/demo/transcript")
    print(json.dumps({"github_comments": len(t["github_comments"]), "slack_messages": len(t["slack_messages"]),
                      "comments": t["github_comments"], "slack": t["slack_messages"]}))


def replay(base: str, demo: Path) -> None:
    parent = run_by_task(base, "Prices like 19.99")
    before = events_count(demo, parent["id"])
    parent_calls = model_calls(demo, parent["id"])
    state = call(base, f"/api/runs/{parent['id']}/replay", {"mode": "state"})
    child = call(base, f"/api/runs/{parent['id']}/replay", {"mode": "model"})["run"]
    done = wait_done(base, child["id"])
    comp = call(base, f"/api/runs/{parent['id']}/replay/{child['id']}/comparison")
    child_calls = model_calls(demo, child["id"])
    live = [c for c in child_calls if c[0] != "recorded"]
    expect(state["ok"], "state replay verifies the ledger", {"phases": state["phases"]})
    expect(not live and len(child_calls) == len(parent_calls) and done["status"] == "completed",
           "model replay makes no live model call and reproduces the call sequence",
           {"original_run": parent["id"], "replay_run": child["id"], "model_calls_in_original": len(parent_calls),
            "model_calls_in_replay": len(child_calls), "LIVE_MODEL_CALLS_DURING_REPLAY": len(live), "replay_status": done["status"],
            "replay_outcome": done.get("outcome"), "comparison": {k: comp[k] for k in comp if k in ("matched", "summary", "differences")}})
    expect(events_count(demo, parent["id"]) == before, "the original run's history is unchanged by replay", {"events_before": before})


def fork(base: str, demo: Path) -> None:
    parent = run_by_task(base, "Round sales tax to the nearest cent in compute_tax")
    parent = [r for r in call(base, "/api/runs") if r["task"].startswith("Round sales tax") and "second attempt" not in r["task"]][-1]
    cps = call(base, f"/api/runs/{parent['id']}/checkpoints")
    seq = next(c["seq"] for c in cps if c["phase"] == "analysis")
    tax = demo / "repos" / "payments-service" / "payments" / "tax.py"
    tax_before = hashlib.sha256(tax.read_bytes()).hexdigest()
    parent_events = events_count(demo, parent["id"])
    child = call(base, f"/api/runs/{parent['id']}/fork",
                 {"from_checkpoint": seq, "model": "demo-tax-fixed", "overrides": {"agent.promote_policy": "never"}})
    done = wait_done(base, child["id"])
    lineage = call(base, f"/api/runs/{parent['id']}/lineage")
    kids = [c for c in lineage["children"] if c["id"] == child["id"]]
    expect(bool(kids) and kids[0]["lineage_kind"] == "fork" and kids[0]["parent_checkpoint_seq"] == seq, "fork lineage is recorded",
           {"parent": parent["id"], "fork": child["id"], "forked_from_checkpoint": seq, "phase": "analysis"})
    expect(done["status"] == "completed" and done.get("verdict") == "passed", "the fork diverges: a different model's patch validates",
           {"parent_outcome": parent.get("outcome"), "parent_verdict": parent.get("verdict"), "fork_status": done["status"],
            "fork_verdict": done.get("verdict"), "fork_outcome": done.get("outcome"), "changed": {"model": "demo-tax-fixed", "agent.promote_policy": "never"}})
    after = call(base, f"/api/runs/{parent['id']}")
    expect(after["status"] == parent["status"] and after.get("outcome") == parent.get("outcome") and events_count(demo, parent["id"]) == parent_events,
           "the parent run is unchanged", {"events": parent_events})
    expect(hashlib.sha256(tax.read_bytes()).hexdigest() == tax_before, "the repository is untouched by the fork (promotion was off)", {"file": "payments/tax.py"})


def metrics(base: str, demo: Path) -> None:
    m = call(base, "/api/metrics")
    print(json.dumps({k: m[k] for k in list(m)[:12]}, default=str)[:1500])


if __name__ == "__main__":
    base, demo, what = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    {"seed": seed, "transcript": transcript, "replay": replay, "fork": fork, "metrics": metrics}[what](base, demo)

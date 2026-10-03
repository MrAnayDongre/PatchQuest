"""Replay a run without its side effects; fork it with a different model or setting.

Parents are never modified, replays never promote, and divergence is reported rather than papered over.
"""

from __future__ import annotations

import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.application.service import ForkBlocked, ForkError
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.persistence import ledger
from patchquest.runtime import lineage
from patchquest.runtime.replay import NotReplayable, ReplayMode, compare_runs, replay_state
from tests.support import CALC_BUG, FIX, PLAN, WRONG, event_types, make_calc_repo, prepare_run, run_row, run_scripted

FIXED = "def add(a, b):\n    return a + b\n"
REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}


@pytest.fixture(autouse=True)
def unattended():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 0
    set_config(cfg)


@pytest.fixture
def repo(tmp_path):
    return make_calc_repo(tmp_path / "repo")


async def finished(svc: TaskService, run_id: str) -> None:
    await svc._tasks[run_id]


def responses(**extra):
    return {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW], **extra}


class TestStateReplay:
    @pytest.mark.asyncio
    async def test_a_normal_run_replays_cleanly_from_its_events_alone(self, repo):
        sm, rid = await run_scripted(repo, responses())
        report = replay_state(rid)
        assert report.ok and report.findings == ()
        assert report.status_trail == ("running", "completed") and report.reconstructed_status == "completed"
        assert report.phases["patching"] == "completed" and report.phases["research"] == "skipped"
        assert report.checkpoints == 12 and report.events > 20

    @pytest.mark.asyncio
    async def test_state_replay_changes_nothing(self, repo):
        sm, rid = await run_scripted(repo, responses())
        with get_db() as conn:
            before = conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0], conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        replay_state(rid)
        with get_db() as conn:
            after = conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0], conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert before == after

    @pytest.mark.asyncio
    async def test_inconsistencies_are_found(self, repo):
        sm, rid = await run_scripted(repo, responses())
        with get_db() as conn:  # the run record disagrees with its own history
            conn.execute("UPDATE runs SET status = 'failed' WHERE id = ?", (rid,))
            conn.execute("UPDATE checkpoints SET checksum = 'x' WHERE run_id = ? AND seq = 3", (rid,))
        report = replay_state(rid)
        assert not report.ok
        assert any("events end in 'completed' but the run record says 'failed'" in f for f in report.findings)
        assert any("checkpoint 3" in f for f in report.findings)

    @pytest.mark.asyncio
    async def test_an_illegal_history_is_detected(self, repo):
        sm, rid = await run_scripted(repo, responses())
        with get_db() as conn:
            ledger.append(conn, rid, "run_state_changed", payload={"from": "completed", "to": "running"}, attempt=1)
        assert any("not a legal transition" in f for f in replay_state(rid).findings)

    def test_unknown_run(self):
        with pytest.raises(LookupError):
            replay_state("nope")


class TestModelReplay:
    @pytest.mark.asyncio
    async def test_recorded_answers_reproduce_the_run_without_calling_a_model_or_touching_the_repo(self, repo):
        sm, rid = await run_scripted(repo, responses())
        assert (repo / "calc.py").read_text() == FIXED
        (repo / "calc.py").write_text(CALC_BUG)  # reset so a replay that promoted would be visible
        calls_before = len(next(s for n, s in ScriptedProvider.scripts.items() if n.endswith(rid)).calls)

        svc = TaskService()
        child = svc.replay(rid, ReplayMode.MODEL)
        await finished(svc, child["id"])

        assert (repo / "calc.py").read_text() == CALC_BUG  # replay never promotes
        assert len(next(s for n, s in ScriptedProvider.scripts.items() if n.endswith(rid)).calls) == calls_before
        row = run_row(child["id"])
        assert row["status"] == "completed" and row["verdict"] == "passed" and row["outcome"] == "rejected"
        assert row["parent_run_id"] == rid and row["lineage_kind"] == "replay" and row["replay_mode"] == "model"
        comparison = compare_runs(rid, child["id"])
        assert comparison.matched, comparison.divergences
        assert "replay_completed" in event_types(child["id"])

    @pytest.mark.asyncio
    async def test_a_repair_loop_replays_identically(self, repo):
        repair = {"edits": [{"path": "calc.py", "search": "return a * b", "replace": "return a + b"}],
                  "create": [], "delete": [], "rationale": ""}
        sm, rid = await run_scripted(repo, responses(coder=[WRONG], repair=[repair]))
        assert run_row(rid)["verdict"] == "passed"
        assert (repo / "calc.py").read_text() == FIXED  # the original promoted, so the repo has moved on
        svc = TaskService()
        child = svc.replay(rid, ReplayMode.MODEL)  # ...yet the replay starts from the files the original saw
        await finished(svc, child["id"])
        assert compare_runs(rid, child["id"]).matched
        assert (repo / "calc.py").read_text() == FIXED
        assert event_types(child["id"]).count("repair_started") == 1

    @pytest.mark.asyncio
    async def test_a_changed_recording_is_reported_as_a_divergence(self, repo):
        sm, rid = await run_scripted(repo, responses())
        with get_db() as conn:  # an operator edits history after the fact (the recording, not the ledger)
            conn.execute("UPDATE model_calls SET response_text = replace(response_text, 'a + b', 'a * b') "
                         "WHERE run_id = ? AND role = 'coder'", (rid,))
        svc = TaskService()
        child = svc.replay(rid, ReplayMode.MODEL)
        await finished(svc, child["id"])
        comparison = compare_runs(rid, child["id"])
        assert not comparison.matched and "diff" in {d.aspect for d in comparison.divergences}
        assert "replay_diverged" in event_types(child["id"])

    @pytest.mark.asyncio
    async def test_running_out_of_recorded_answers_stops_the_replay(self, repo):
        sm, rid = await run_scripted(repo, responses())
        with get_db() as conn:
            conn.execute("DELETE FROM model_calls WHERE run_id = ? AND role = 'reviewer'", (rid,))
        svc = TaskService()
        child = svc.replay(rid, ReplayMode.MODEL)
        await finished(svc, child["id"])
        row = run_row(child["id"])
        assert row["status"] == "failed" and row["failure_kind"] == "REPLAY_DIVERGED"

    @pytest.mark.asyncio
    async def test_a_run_without_retained_model_output_cannot_be_replayed(self, repo):
        cfg = AppConfig()
        cfg.agent.record_model_io = False
        set_config(cfg)
        sm, rid = await run_scripted(repo, responses())
        with pytest.raises(NotReplayable, match="not retained"):
            TaskService().replay(rid, ReplayMode.MODEL)

    @pytest.mark.asyncio
    async def test_a_run_with_no_model_calls_cannot_be_replayed(self, repo):
        sm, rid = prepare_run(repo, responses())
        with pytest.raises(NotReplayable, match="no recorded model calls"):
            TaskService().replay(rid, ReplayMode.MODEL)


class TestLiveReplay:
    @pytest.mark.asyncio
    async def test_live_replay_asks_the_model_again_and_still_never_promotes(self, repo):
        sm, rid = await run_scripted(repo, responses(coder=[FIX, WRONG]))
        (repo / "calc.py").write_text(CALC_BUG)
        svc = TaskService()
        child = svc.replay(rid, ReplayMode.LIVE)  # same scripted model, which now answers differently
        await finished(svc, child["id"])
        assert (repo / "calc.py").read_text() == CALC_BUG
        comparison = compare_runs(rid, child["id"])
        assert not comparison.matched  # the live model said something else
        assert run_row(child["id"])["replay_mode"] == "live" and "replay_diverged" in event_types(child["id"])


class TestFork:
    @pytest.mark.asyncio
    async def test_fork_from_a_checkpoint_with_another_model_leaves_the_parent_alone(self, repo):
        sm, rid = await run_scripted(repo, responses())
        parent_events = len(event_types(rid))
        (repo / "calc.py").write_text(CALC_BUG)
        with get_db() as conn:
            seq = conn.execute("SELECT seq FROM checkpoints WHERE run_id = ? AND phase = 'analysis'", (rid,)).fetchone()[0]

        # the alternative model fixes it differently, via a different script registered under another name
        alt = "alt-model"
        ScriptedProvider.register(alt, {"coder": [{"edits": [{"path": "calc.py", "search": "return a - b",
                                                              "replace": "return b + a"}], "create": [], "delete": [], "rationale": ""}],
                                        "reviewer": [REVIEW]})
        svc = TaskService()
        child = svc.fork(rid, from_seq=seq, model=alt, overrides={"agent.promote_policy": "always"})
        await finished(svc, child["id"])

        assert len(event_types(rid)) == parent_events  # the parent's history did not move
        row = run_row(child["id"])
        assert row["status"] == "completed" and row["outcome"] == "applied"
        assert row["parent_run_id"] == rid and row["parent_checkpoint_seq"] == seq and row["lineage_kind"] == "fork"
        assert row["model"] == alt and row["provider"] == "scripted"
        assert (repo / "calc.py").read_text() == "def add(a, b):\n    return b + a\n"  # the child's patch, not the parent's
        # phases settled before the fork point were not repeated
        phases = [e["phase"] for e in __import__("tests.support", fromlist=["fetch_events"]).fetch_events(child["id"])
                  if e["type"] == "phase_started"]
        assert phases[0] == "patching" and "intake" not in phases

    @pytest.mark.asyncio
    async def test_the_child_keeps_its_own_identity_not_the_parents(self, repo):
        sm, rid = await run_scripted(repo, responses())
        (repo / "calc.py").write_text(CALC_BUG)
        ScriptedProvider.register("alt", {"coder": [FIX], "reviewer": [REVIEW]})
        svc = TaskService()
        child = svc.fork(rid, model="alt", from_seq=6)
        await finished(svc, child["id"])
        with get_db() as conn:
            assert {r[0] for r in conn.execute("SELECT DISTINCT run_id FROM model_calls WHERE model = 'alt' OR run_id = ?",
                                               (child["id"],))} <= {child["id"], rid}
            assert conn.execute("SELECT COUNT(*) FROM model_calls WHERE run_id = ?", (child["id"],)).fetchone()[0] > 0
        assert child["id"] != rid

    @pytest.mark.asyncio
    async def test_overrides_apply_to_the_fork_only(self, repo):
        sm, rid = await run_scripted(repo, responses())
        (repo / "calc.py").write_text(CALC_BUG)
        ScriptedProvider.register("alt", {"coder": [FIX]})
        svc = TaskService()
        child = svc.fork(rid, model="alt", from_seq=6, overrides={"agent.max_model_calls": 1})
        await finished(svc, child["id"])
        assert run_row(child["id"])["failure_kind"] == "BUDGET_EXHAUSTED"  # the fork's own limit bit...
        from patchquest.config import get_config

        assert get_config().agent.max_model_calls == 40  # ...and nothing leaked into the global config

    def test_unsafe_or_unknown_overrides_are_refused_before_anything_is_created(self, repo):
        with get_db() as conn:
            pass
        sm, rid = prepare_run(repo, responses())
        for bad in ({"safety.approval_timeout_seconds": 1}, {"agent.nonexistent": 1}, {"agent.max_model_calls": "many"},
                    {"agent.x.y": 1}):
            with pytest.raises(ValueError):
                with get_db() as conn:
                    lineage.create_child(conn, rid, kind="fork", parent_cp=None, overrides=bad)

    @pytest.mark.asyncio
    async def test_fork_refuses_without_a_checkpoint_or_with_a_missing_one(self, repo):
        sm, rid = prepare_run(repo, responses())
        svc = TaskService()
        with pytest.raises(ForkError, match="no usable checkpoint"):
            svc.fork(rid)
        sm2, done = await run_scripted(repo, responses())
        with pytest.raises(ForkError, match="checkpoint 99"):
            svc.fork(done, from_seq=99)

    @pytest.mark.asyncio
    async def test_fork_is_blocked_when_the_repository_changed_under_the_checkpoint(self, repo):
        sm, rid = await run_scripted(repo, responses())
        # the run promoted its patch, so calc.py no longer matches what the checkpoint recorded
        with get_db() as conn:
            seq = conn.execute("SELECT seq FROM checkpoints WHERE run_id = ? AND phase = 'patching'", (rid,)).fetchone()[0]
        (repo / "calc.py").write_text("def add(a, b):\n    return 0  # edited by a person\n")
        svc = TaskService()
        with pytest.raises(ForkBlocked):
            svc.fork(rid, from_seq=seq)
        ScriptedProvider.register("alt", {"coder": [FIX], "reviewer": [REVIEW]})
        child = svc.fork(rid, from_seq=seq, model="alt", accept_drift=True)
        await finished(svc, child["id"])
        assert "edited by a person" in (repo / "calc.py").read_text()  # promotion still refused to overwrite

    @pytest.mark.asyncio
    async def test_a_fork_can_itself_be_resumed_from_its_fork_point(self, repo):
        from patchquest.persistence import checkpoints

        sm, rid = await run_scripted(repo, responses())
        (repo / "calc.py").write_text(CALC_BUG)
        ScriptedProvider.register("alt", {"coder": [FIX], "reviewer": [REVIEW]})
        svc = TaskService()
        child = svc.fork(rid, model="alt", from_seq=6)
        await finished(svc, child["id"])
        with get_db() as conn:
            first = conn.execute("SELECT * FROM checkpoints WHERE run_id = ? AND seq = 1", (child["id"],)).fetchone()
            assert first["phase"] == "analysis"  # a copy of the parent's checkpoint, re-signed for the child
            assert checkpoints.get(conn, child["id"], 1).state["ctx"]["plan"]["plan"]["plan"] == "fix add"

    @pytest.mark.asyncio
    async def test_lineage_is_navigable_both_ways(self, repo):
        sm, rid = await run_scripted(repo, responses())
        (repo / "calc.py").write_text(CALC_BUG)
        ScriptedProvider.register("alt", {"coder": [FIX], "reviewer": [REVIEW]})
        svc = TaskService()
        child = svc.fork(rid, model="alt", from_seq=6)
        await finished(svc, child["id"])
        grandchild = svc.replay(child["id"], ReplayMode.MODEL)
        await finished(svc, grandchild["id"])
        assert [r["id"] for r in svc.lineage(grandchild["id"])["ancestry"]] == [rid, child["id"], grandchild["id"]]
        assert [c["id"] for c in svc.lineage(rid)["children"]] == [child["id"]]

"""Kill a run at precise points, restart, and check what resume does (and refuses to do).

Every test asserts a *safety property* about the repository or the ledger, not merely that the run finishes.
``SimulatedCrash`` stands in for the process dying right after an event is committed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.config import AppConfig, set_config
from patchquest.database import get_db
from patchquest.persistence import checkpoints, ledger
from patchquest.runtime.fingerprint import Drift
from patchquest.runtime.resume import (
    ConfirmationRequired,
    NotResumable,
    PromotionState,
    RecoveryCategory,
    plan_resume,
)
from tests.support import (
    CALC_BUG,
    FIX,
    PLAN,
    after_event,
    crash_run,
    make_calc_repo,
    resume_run,
    run_row,
    run_scripted,
)

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


def git_init(repo):
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True)


def script(run_id):
    return next(s for n, s in ScriptedProvider.scripts.items() if n.endswith(run_id))


def types(run_id):
    with get_db() as conn:
        return [e["type"] for e in ledger.read(conn, run_id)]


def trail(run_id):
    with get_db() as conn:
        return [e["payload"]["to"] for e in ledger.read(conn, run_id) if e["type"] == "run_state_changed"]


def responses(**extra):
    return {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW], **extra}


class TestResumeFromCheckpoint:
    @pytest.mark.asyncio
    async def test_completed_phases_are_not_repeated_and_the_model_is_not_called_again(self, repo):
        rid = await crash_run(repo, responses(), after_event("checkpoint_created", phase="patching"))
        assert run_row(rid)["status"] == "interrupted"
        assert (repo / "calc.py").read_text() == CALC_BUG  # nothing reached the real repository yet

        plan = await resume_run(rid)
        assert plan.category is RecoveryCategory.SAFE_RESUME and plan.next_phase == "static_checks"
        assert (repo / "calc.py").read_text() == FIXED
        row = run_row(rid)
        assert row["status"] == "completed" and row["outcome"] == "applied" and row["verdict"] == "passed"
        assert len(script(rid).calls_for("coder")) == 1  # the patch came from the checkpoint, not a second model call
        assert len(script(rid).calls_for("planner")) == 1
        assert trail(rid) == ["running", "interrupted", "running", "completed"]
        assert row["attempt"] == 2

    @pytest.mark.asyncio
    async def test_the_shadow_workspace_is_rebuilt_from_the_checkpoint(self, repo):
        rid = await crash_run(repo, responses(), after_event("checkpoint_created", phase="patching"))
        with get_db() as conn:
            cp, _ = checkpoints.latest_valid(conn, rid)
        assert cp is not None and set(cp.state["workspace"]) == {"calc.py"}
        await resume_run(rid)
        # tests ran against the restored patched workspace and passed, so the patch survived the crash
        assert "tests_completed" in types(rid) and run_row(rid)["verdict"] == "passed"

    @pytest.mark.asyncio
    async def test_crash_mid_command_reruns_only_that_phase_and_says_what_was_interrupted(self, repo):
        rid = await crash_run(repo, responses(), after_event("command_started", phase=None))
        plan = plan_resume(rid)
        assert plan.interrupted_operation and "command" in plan.interrupted_operation
        assert plan.category is RecoveryCategory.SAFE_RESUME
        await resume_run(rid)
        assert run_row(rid)["status"] == "completed" and (repo / "calc.py").read_text() == FIXED
        assert len(script(rid).calls_for("coder")) == 1  # the patch phase was not repeated

    @pytest.mark.asyncio
    async def test_crash_before_any_checkpoint_restarts_from_the_beginning(self, repo):
        rid = await crash_run(repo, responses(), after_event("phase_started", phase="intake"))
        plan = plan_resume(rid)
        assert plan.category is RecoveryCategory.SAFE_RETRY and plan.checkpoint is None
        await resume_run(rid)
        assert run_row(rid)["status"] == "completed" and (repo / "calc.py").read_text() == FIXED

    @pytest.mark.asyncio
    async def test_resume_is_recorded_in_the_ledger_with_the_plan(self, repo):
        rid = await crash_run(repo, responses(), after_event("checkpoint_created", phase="planning"))
        await resume_run(rid)
        with get_db() as conn:
            event = next(e for e in ledger.read(conn, rid) if e["type"] == "run_resume_requested")
        assert event["actor"] == "user" and event["attempt"] == 2
        assert event["payload"]["CATEGORY"] == "SAFE_RESUME" and event["payload"]["APPROVAL_REQUIRED"] is False
        with get_db() as conn:
            assert {e["attempt"] for e in ledger.read(conn, rid)} == {1, 2}


class TestRefusals:
    @pytest.mark.asyncio
    async def test_a_finished_run_cannot_be_resumed(self, repo):
        sm, rid = await run_scripted(repo, responses())
        plan = plan_resume(rid)
        assert plan.category is RecoveryCategory.NON_RECOVERABLE
        with pytest.raises(NotResumable):
            await resume_run(rid)

    @pytest.mark.asyncio
    async def test_a_running_run_cannot_be_resumed_underneath_itself(self, repo):
        from tests.support import insert_run

        insert_run("live", status="running")
        assert plan_resume("live").category is RecoveryCategory.NON_RECOVERABLE
        assert "still active" in plan_resume("live").reasons[0]

    def test_unknown_run(self):
        with pytest.raises(LookupError):
            plan_resume("does-not-exist")


class TestRepositoryDrift:
    @pytest.mark.asyncio
    async def test_human_edit_to_a_file_the_agent_changes_is_never_overwritten(self, repo):
        rid = await crash_run(repo, responses(), after_event("checkpoint_created", phase="patching"))
        (repo / "calc.py").write_text("def add(a, b):\n    # fixed by hand\n    return b + a\n")

        plan = plan_resume(rid)
        assert plan.category is RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED
        assert plan.drift.kind is Drift.CONFLICTING_DRIFT and plan.drift.conflicting == ("calc.py",)
        with pytest.raises(ConfirmationRequired):
            await resume_run(rid)
        assert run_row(rid)["status"] == "interrupted"  # refusing changed nothing

        await resume_run(rid, accept_drift=True)
        assert "fixed by hand" in (repo / "calc.py").read_text()  # the human's version survives
        row = run_row(rid)
        assert row["outcome"] == "conflict" and "patch_applied" not in types(rid)

    @pytest.mark.asyncio
    async def test_edit_to_an_unrelated_file_resumes_without_asking(self, repo):
        git_init(repo)
        rid = await crash_run(repo, responses(), after_event("checkpoint_created", phase="patching"))
        (repo / "NOTES.md").write_text("added while the run was down\n")
        plan = plan_resume(rid)
        assert plan.category is RecoveryCategory.SAFE_RESUME and plan.drift.kind is Drift.SAFE_DRIFT
        await resume_run(rid)
        assert (repo / "calc.py").read_text() == FIXED
        assert (repo / "NOTES.md").read_text() == "added while the run was down\n"


class TestPromotionJournal:
    TWO_FILES = {"edits": [{"path": "calc.py", "search": "return a - b", "replace": "return a + b"}],
                 "create": [{"path": "extra.py", "content": "X = 1\n"}], "delete": [], "rationale": ""}

    @pytest.mark.asyncio
    async def test_crash_before_the_write_applies_it_exactly_once_on_resume(self, repo):
        rid = await crash_run(repo, responses(), after_event("promotion_started"))
        assert (repo / "calc.py").read_text() == CALC_BUG
        plan = plan_resume(rid)
        assert plan.promotion is PromotionState.NOT_APPLIED and plan.category is RecoveryCategory.SAFE_RESUME
        await resume_run(rid)
        assert (repo / "calc.py").read_text() == FIXED and run_row(rid)["outcome"] == "applied"
        assert types(rid).count("promotion_completed") == 1

    @pytest.mark.asyncio
    async def test_crash_after_the_write_is_reconciled_not_repeated(self, repo):
        rid = await crash_run(repo, responses(), after_event("promotion_completed"))
        assert (repo / "calc.py").read_text() == FIXED  # it did land
        plan = plan_resume(rid)
        assert plan.promotion is PromotionState.APPLIED and plan.side_effects.value == "VERIFIED"
        await resume_run(rid)
        row = run_row(rid)
        assert row["status"] == "completed" and row["outcome"] == "applied"
        assert types(rid).count("promotion_started") == 1 and "promotion_reconciled" in types(rid)
        assert (repo / "calc.py").read_text() == FIXED

    @pytest.mark.asyncio
    async def test_half_written_promotion_demands_rollback_and_then_completes_cleanly(self, repo):
        rid = await crash_run(repo, responses(coder=[self.TWO_FILES]), after_event("promotion_started"))
        (repo / "calc.py").write_text(FIXED)  # simulate: the first file landed, the second did not
        plan = plan_resume(rid)
        assert plan.promotion is PromotionState.PARTIAL and plan.category is RecoveryCategory.ROLLBACK_REQUIRED
        assert plan.side_effects.value == "UNCERTAIN"
        with pytest.raises(ConfirmationRequired):
            await resume_run(rid)
        assert (repo / "calc.py").read_text() == FIXED  # nothing was touched by the refusal

        await resume_run(rid, rollback=True)
        assert "promotion_rolled_back" in types(rid)
        row = run_row(rid)
        assert row["status"] == "completed" and row["outcome"] == "applied"
        assert (repo / "calc.py").read_text() == FIXED and (repo / "extra.py").read_text() == "X = 1\n"

    @pytest.mark.asyncio
    async def test_a_file_edited_by_a_human_mid_promotion_is_never_rolled_back(self, repo):
        rid = await crash_run(repo, responses(coder=[self.TWO_FILES]), after_event("promotion_started"))
        # calc.py: neither the original nor what PatchQuest meant to write, because a human edited it
        (repo / "calc.py").write_text("def add(a, b):\n    return sum((a, b))  # mine\n")
        plan = plan_resume(rid)
        assert plan.promotion is PromotionState.UNKNOWN and plan.category is RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED
        await resume_run(rid, accept_drift=True)
        assert "mine" in (repo / "calc.py").read_text()  # still the human's version
        assert run_row(rid)["outcome"] == "conflict"  # promotion refused to overwrite it

    @pytest.mark.asyncio
    async def test_promotion_with_no_checkpoint_to_reconcile_against_needs_a_human(self, repo):
        rid = await crash_run(repo, responses(), after_event("promotion_completed"))
        with get_db() as conn:
            conn.execute("DELETE FROM checkpoints WHERE run_id = ?", (rid,))
        assert plan_resume(rid).category is RecoveryCategory.HUMAN_CONFIRMATION_REQUIRED


class TestCheckpointIntegrity:
    def _corrupt_newest(self, rid, how):
        with get_db() as conn:
            row = conn.execute("SELECT id, state_json FROM checkpoints WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                               (rid,)).fetchone()
            if how == "truncate":
                conn.execute("UPDATE checkpoints SET state_json = ? WHERE id = ?", (row["state_json"][: len(row["state_json"]) // 2], row["id"]))
            elif how == "flip":
                data = json.loads(row["state_json"])
                data["ctx"]["task"] = "tampered"
                conn.execute("UPDATE checkpoints SET state_json = ? WHERE id = ?", (json.dumps(data), row["id"]))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("how", ["truncate", "flip"])
    async def test_a_damaged_newest_checkpoint_is_skipped_for_the_previous_good_one(self, repo, how):
        # the older checkpoint predates the patch, so the coder legitimately runs a second time
        rid = await crash_run(repo, responses(coder=[FIX, FIX]), after_event("checkpoint_created", phase="patching"))
        with get_db() as conn:
            newest = conn.execute("SELECT MAX(seq) FROM checkpoints WHERE run_id = ?", (rid,)).fetchone()[0]
        self._corrupt_newest(rid, how)
        plan = plan_resume(rid)
        assert plan.checkpoint["seq"] == newest - 1 and plan.invalid_checkpoints
        assert "checksum mismatch" in plan.invalid_checkpoints[0] or "unreadable" in plan.invalid_checkpoints[0]
        await resume_run(rid)
        assert run_row(rid)["status"] == "completed" and (repo / "calc.py").read_text() == FIXED

    @pytest.mark.asyncio
    async def test_every_checkpoint_damaged_falls_back_to_a_clean_restart(self, repo):
        rid = await crash_run(repo, responses(planner=[PLAN, PLAN]), after_event("checkpoint_created", phase="context_building"))
        with get_db() as conn:
            conn.execute("UPDATE checkpoints SET checksum = 'bad' WHERE run_id = ?", (rid,))
        plan = plan_resume(rid)
        assert plan.category is RecoveryCategory.SAFE_RETRY and plan.checkpoint is None and plan.invalid_checkpoints
        await resume_run(rid)
        assert run_row(rid)["status"] == "completed"


class TestCrashRecoveryAtStartup:
    @pytest.mark.asyncio
    async def test_cancel_requested_before_the_crash_is_honoured_not_resumed(self, repo):
        sm_rid = await crash_run(repo, responses(), after_event("checkpoint_created", phase="planning"))
        # the user had asked to cancel just before the process died
        with get_db() as conn:
            conn.execute("UPDATE runs SET status = 'cancel_requested' WHERE id = ?", (sm_rid,))
        from patchquest.recovery import recover_interrupted_runs

        assert recover_interrupted_runs() == 1
        assert run_row(sm_rid)["status"] == "cancelled"
        assert plan_resume(sm_rid).category is RecoveryCategory.NON_RECOVERABLE


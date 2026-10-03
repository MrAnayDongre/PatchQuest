"""Cancellation reaches whatever is actually running: subprocess trees, model calls, approval waits."""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from patchquest.config import AppConfig, set_config
from patchquest.execution.executor import run_argv
from patchquest.persistence import ledger
from tests.support import CALC_BUG, FIX, PLAN, fetch_events, make_calc_repo, run_row, run_scripted

REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}


@pytest.fixture(autouse=True)
def config():
    cfg = AppConfig()
    cfg.safety.approval_timeout_seconds = 30
    set_config(cfg)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until_dead(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


class TestExecutor:
    def test_cancel_kills_the_whole_process_tree_promptly(self, tmp_path):
        pidfile = tmp_path / "pids"
        script = (f"import os, subprocess, sys, time\n"
                  f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                  f"open({str(pidfile)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
                  f"time.sleep(60)\n")
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        t0 = time.monotonic()
        result = run_argv([os.sys.executable, "-c", script], str(tmp_path), timeout=60, cancel=cancel)
        assert time.monotonic() - t0 < 5 and result["cancelled"] and not result["success"] and result["returncode"] == -1
        parent, child = map(int, pidfile.read_text().split())
        assert wait_until_dead(parent) and wait_until_dead(child)  # no orphan keeps running

    def test_a_command_that_finishes_is_not_marked_cancelled(self, tmp_path):
        result = run_argv(["true"], str(tmp_path), cancel=threading.Event())
        assert result["success"] and result["cancelled"] is False

    def test_cancel_set_before_start_still_kills_a_long_command(self, tmp_path):
        cancel = threading.Event()
        cancel.set()
        t0 = time.monotonic()
        result = run_argv(["sleep", "30"], str(tmp_path), timeout=60, cancel=cancel)
        assert result["cancelled"] and time.monotonic() - t0 < 3

    def test_timeout_still_works_and_is_not_reported_as_cancel(self, tmp_path):
        result = run_argv(["sleep", "30"], str(tmp_path), timeout=0.3, cancel=threading.Event())
        assert result["timed_out"] and result["cancelled"] is False


class TestRun:
    @pytest.mark.asyncio
    async def test_cancel_during_a_running_command_stops_it_within_seconds(self, tmp_path):
        repo = make_calc_repo(tmp_path / "repo")
        pidfile = tmp_path / "pid"
        (repo / "tests" / "test_slow.py").write_text(
            f"import os, time, unittest\n\nclass T(unittest.TestCase):\n    def test_slow(self):\n"
            f"        open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n        time.sleep(60)\n")

        async def canceller(sm, rid):
            for _ in range(600):
                if any(e["type"] == "command_started" for e in fetch_events(rid)) and pidfile.exists():
                    sm.cancel()
                    return
                await asyncio.sleep(0.02)

        t0 = time.monotonic()
        sm, rid = await run_scripted(repo, {"planner": [PLAN], "coder": [FIX]}, wait_for=canceller)
        assert time.monotonic() - t0 < 15
        row = run_row(rid)
        assert row["status"] == "cancelled" and row["failure_kind"] == "USER_CANCELLED"
        assert wait_until_dead(int(pidfile.read_text()))
        assert (repo / "calc.py").read_text() == CALC_BUG

    @pytest.mark.asyncio
    async def test_cancel_interrupts_a_model_call_in_flight(self, tmp_path):
        repo = make_calc_repo(tmp_path / "repo")
        started = asyncio.Event()

        async def hung_planner(_messages):
            started.set()
            await asyncio.sleep(60)

        async def canceller(sm, rid):
            await started.wait()
            sm.cancel()

        t0 = time.monotonic()
        sm, rid = await run_scripted(repo, {"planner": [hung_planner]}, wait_for=canceller)
        assert time.monotonic() - t0 < 5
        assert run_row(rid)["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_while_waiting_for_approval_denies_and_stops(self, tmp_path):
        repo = make_calc_repo(tmp_path / "repo")
        wrong = {"edits": [{"path": "calc.py", "search": "return a - b", "replace": "return a * b"}],
                 "create": [], "delete": [], "rationale": ""}
        empty = {"edits": [], "create": [], "delete": [], "rationale": ""}  # unrepairable: promotion needs a human

        async def canceller(sm, rid):
            for _ in range(600):
                if any(e["type"] == "approval_requested" for e in fetch_events(rid)):
                    sm.cancel()
                    return
                await asyncio.sleep(0.02)
            raise AssertionError("the run never asked for approval")

        t0 = time.monotonic()
        sm, rid = await run_scripted(repo, {"planner": [PLAN], "coder": [wrong], "repair": [empty, empty]},
                                     wait_for=canceller)
        assert time.monotonic() - t0 < 10  # not the 30s approval timeout
        assert run_row(rid)["status"] == "cancelled"
        assert (repo / "calc.py").read_text() == CALC_BUG  # the unapproved patch never reached the repository
        assert "patch_applied" not in [e["type"] for e in fetch_events(rid)]

    @pytest.mark.asyncio
    async def test_a_promotion_in_progress_is_never_cut_off_half_way(self, tmp_path, monkeypatch):
        from patchquest.runtime.workspace import ShadowWorkspace

        repo = make_calc_repo(tmp_path / "repo")
        real = ShadowWorkspace.promote

        def slow_promote(self):
            time.sleep(0.6)  # long enough for the cancel to land mid-promotion
            return real(self)

        monkeypatch.setattr(ShadowWorkspace, "promote", slow_promote)

        async def canceller(sm, rid):
            for _ in range(600):
                if any(e["type"] == "promotion_started" for e in fetch_events(rid)):
                    sm.cancel()
                    return
                await asyncio.sleep(0.01)

        sm, rid = await run_scripted(repo, {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]}, wait_for=canceller)
        types = [e["type"] for e in fetch_events(rid)]
        assert "promotion_completed" in types  # the write finished and was journaled
        assert (repo / "calc.py").read_text().endswith("a + b\n")
        from patchquest.database import get_db

        with get_db() as conn:
            trail = [e["payload"]["to"] for e in ledger.read(conn, rid) if e["type"] == "run_state_changed"]
        assert "cancel_requested" in trail and run_row(rid)["status"] in ("completed", "cancelled")

    @pytest.mark.asyncio
    async def test_cancelling_twice_or_after_the_end_is_harmless(self, tmp_path):
        repo = make_calc_repo(tmp_path / "repo")
        sm, rid = await run_scripted(repo, {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
        assert run_row(rid)["status"] == "completed"
        sm.cancel()
        sm.cancel()
        assert run_row(rid)["status"] == "completed"  # a finished run stays finished


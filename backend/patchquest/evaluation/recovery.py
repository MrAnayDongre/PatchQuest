"""Recovery scenarios: does a run that is killed, edited under, or damaged end up *correct and safe*?

Each scenario runs a real corpus task with the scripted reference model, makes something go wrong at an exact point
(a crash after an event, a human edit, a damaged checkpoint, a cancel), asks the resume planner what it makes of it,
resumes, and checks the safety invariants: the repository is patched exactly once or not at all, a human's edit is
never overwritten, completed phases are not repeated, and what could not be proven safe waits for a person.
Deterministic, CPU only, no model.
"""

from __future__ import annotations

import asyncio
import shlex
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService
from patchquest.config import get_config
from patchquest.database import get_db, get_db_path, init_db, set_db_path
from patchquest.evaluation.faults import SimulatedCrash, after_event, crash_when
from patchquest.evaluation.runner import ORACLE_TIMEOUT, _materialize, _script_for
from patchquest.evaluation.tasks import EvalTask, load_corpus
from patchquest.execution.executor import run_argv, scrubbed_env
from patchquest.recovery import recover_interrupted_runs
from patchquest.runtime.resume import NotResumable, plan_resume

TASK_ID = "bugfix-leap-year"
HUMAN_MARK = "# edited by a person while the run was down"


@dataclass(frozen=True)
class Scenario:
    id: str
    description: str
    crash: Callable[[dict], bool] | None  # kill the run right after the first matching event (None: let it finish)
    interfere: str | None = None  # "human_edit" | "corrupt_newest_checkpoint" | None
    resume_with: dict[str, bool] = field(default_factory=dict)  # accept_drift / rollback
    expect_category: str | None = None  # what plan_resume should say
    expect_status: str = "completed"
    expect_outcome: str | None = "applied"
    expect_human_edit_kept: bool = False
    expect_promotions: int | None = 1  # times the repository was actually written (promotion_completed) across the history
    cancel: bool = False
    allow_extra_calls: int = 0  # model calls beyond an uninterrupted run that are legitimate (a phase re-run from an older checkpoint)


BASELINE = Scenario("uninterrupted", "the same task with nothing going wrong (the cost to compare against)", None, expect_category=None)

SCENARIOS: tuple[Scenario, ...] = (
    Scenario("crash-after-patching", "killed right after the patch phase checkpoint",
             after_event("checkpoint_created", phase="patching"), expect_category="SAFE_RESUME"),
    Scenario("crash-mid-command", "killed while a test command was running",
             after_event("command_started"), expect_category="SAFE_RESUME"),
    Scenario("crash-before-promotion-write", "killed after the promotion was journaled, before the repository was written",
             after_event("promotion_started"), expect_category="SAFE_RESUME"),
    Scenario("crash-after-promotion-write", "killed after the repository was written, before the run said so",
             after_event("promotion_completed"), expect_category="SAFE_RESUME"),
    Scenario("crash-before-any-checkpoint", "killed in the very first phase",
             after_event("phase_started", phase="intake"), expect_category="SAFE_RETRY"),
    Scenario("human-edits-the-same-file", "a person edits the file the agent changes while the run is down",
             after_event("checkpoint_created", phase="patching"), interfere="human_edit", resume_with={"accept_drift": True},
             expect_category="HUMAN_CONFIRMATION_REQUIRED", expect_outcome="conflict", expect_human_edit_kept=True, expect_promotions=0),
    Scenario("damaged-newest-checkpoint", "the newest checkpoint is corrupted",
             after_event("checkpoint_created", phase="patching"), interfere="corrupt_newest_checkpoint", expect_category="SAFE_RESUME", allow_extra_calls=1),
    Scenario("cancelled-midway", "the user cancels while the patch is being validated",
             after_event("patch_staged"), cancel=True, expect_status="cancelled", expect_outcome="rejected", expect_promotions=0),
)


@dataclass
class ScenarioResult:
    id: str
    description: str
    passed: bool
    failures: list[str]
    category: str | None
    status: str | None
    outcome: str | None
    model_calls: int
    wall_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _events(run_id: str) -> list[str]:
    with get_db() as conn:
        return [r[0] for r in conn.execute("SELECT type FROM run_events WHERE run_id = ? ORDER BY id", (run_id,))]


async def _run_scenario(sc: Scenario, task: EvalTask, root: Path, clean_calls: int | None) -> ScenarioResult:
    started = time.monotonic()
    repo = root / sc.id
    _materialize(repo, task.files)
    model = f"recovery-{sc.id}"
    script = _script_for(task)
    ScriptedProvider.register(model, {**{k: v * 3 for k, v in script.items()},
                                      "reviewer": [{"minimal_change": True, "unrelated_changes": False, "risk_notes": "",
                                                    "missing_tests": [], "recommendation": "approve"}] * 3})
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task=task.task, provider="scripted", model=model)
    run_id = run["id"]
    machine = svc._machine_for(run)
    svc._machines[run_id] = machine
    touched = repo / task.solution["edits"][0]["path"]

    failures: list[str] = []
    category: str | None = None
    if sc.crash is None:
        await machine.execute()
    elif sc.cancel:
        cancelled = asyncio.Event()

        def trigger(event: dict) -> bool:
            hit = sc.crash(event)  # type: ignore[misc]
            if hit:
                machine.cancel()
                cancelled.set()
            return False  # cancellation is a request, not a crash

        with crash_when(trigger):
            await machine.execute()
    else:
        try:
            with crash_when(sc.crash):
                await machine.execute()
            failures.append("the run finished without reaching the crash point")
        except SimulatedCrash:
            pass
        svc._machines.pop(run_id, None)
        recover_interrupted_runs()
        if sc.interfere == "human_edit":
            touched.write_text(touched.read_text() + f"\n{HUMAN_MARK}\n")
        elif sc.interfere == "corrupt_newest_checkpoint":
            with get_db() as conn:
                newest = conn.execute("SELECT MAX(seq) FROM checkpoints WHERE run_id = ?", (run_id,)).fetchone()[0]
                conn.execute("UPDATE checkpoints SET checksum = 'damaged' WHERE run_id = ? AND seq = ?", (run_id, newest))
        category = plan_resume(run_id).category.value
        try:
            svc.resume(run_id, **sc.resume_with)
            await svc._tasks[run_id]
        except NotResumable as exc:
            failures.append(f"resume was refused: {exc}")

    final = svc.get_run(run_id)
    oracle_ok = None
    if final.get("outcome") == "applied":
        _materialize(repo, task.oracle_files)
        oracle_ok = run_argv(shlex.split(task.oracle_command), str(repo), timeout=ORACLE_TIMEOUT, env=scrubbed_env())["success"]

    if sc.expect_category and category != sc.expect_category:
        failures.append(f"the resume plan said {category}, expected {sc.expect_category}")
    if final["status"] != sc.expect_status:
        failures.append(f"status is {final['status']}, expected {sc.expect_status}")
    if sc.expect_outcome and final.get("outcome") != sc.expect_outcome:
        failures.append(f"outcome is {final.get('outcome')}, expected {sc.expect_outcome}")
    if sc.expect_outcome == "applied" and not oracle_ok:
        failures.append("the repository does not pass the hidden checks after recovery")
    if sc.expect_human_edit_kept and HUMAN_MARK not in touched.read_text():
        failures.append("a person's edit was overwritten")
    events = _events(run_id)
    writes = events.count("promotion_completed")
    if sc.expect_promotions is not None and writes != sc.expect_promotions:
        failures.append(f"the repository was written {writes} time(s), expected {sc.expect_promotions}")
    with get_db() as conn:
        calls = conn.execute("SELECT COUNT(*) FROM model_calls WHERE run_id = ? AND status = 'ok'", (run_id,)).fetchone()[0]
    if clean_calls is not None and calls > clean_calls + sc.allow_extra_calls:
        failures.append(f"{calls} model calls vs {clean_calls} for an uninterrupted run: completed work was repeated")
    return ScenarioResult(sc.id, sc.description, not failures, failures, category, final["status"], final.get("outcome"), calls,
                          round(time.monotonic() - started, 2))


async def run_recovery(*, only: str | None = None) -> dict[str, Any]:
    task = next(t for t in load_corpus() if t.id == TASK_ID)
    scenarios = [s for s in SCENARIOS if not only or only in s.id]
    config = get_config()
    saved = config.safety.approval_timeout_seconds
    config.safety.approval_timeout_seconds = 0
    previous = get_db_path()
    results: list[ScenarioResult] = []  # the scenarios; the uninterrupted baseline is reported separately
    with tempfile.TemporaryDirectory(prefix="pq-recovery-") as tmp:
        set_db_path(Path(tmp) / "recovery.db")
        init_db()
        try:
            baseline = await _run_scenario(BASELINE, task, Path(tmp) / "repos", None)
            for sc in scenarios:
                results.append(await _run_scenario(sc, task, Path(tmp) / "repos", baseline.model_calls))
        finally:
            config.safety.approval_timeout_seconds = saved
            set_db_path(previous)
    passed = sum(1 for r in results if r.passed)
    return {"class": "recovery", "baseline": baseline.to_dict(), "scenarios": [r.to_dict() for r in results],
            "summary": {"scenarios": len(results), "passed": passed, "pass_rate": round(passed / len(results), 4) if results else None}}

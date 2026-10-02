"""Run an evaluation corpus through the real pipeline and score it against hidden oracles."""

from __future__ import annotations

import asyncio
import hashlib
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application.service import TaskService
from patchquest.config import get_config
from patchquest.database import get_db, init_db, set_db_path
from patchquest.evaluation.metrics import RESULT_SCHEMA, EvalReport, TaskResult, classify, summarize
from patchquest.evaluation.tasks import EvalTask, corpus_digest, load_corpus
from patchquest.execution.executor import run_argv, scrubbed_env

ORACLE_TIMEOUT = 120


def _materialize(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _script_for(task: EvalTask) -> dict[str, list[Any]]:
    sol = task.solution
    return {
        "planner": [{"plan": "reference solution", "files_to_inspect": sol.get("files_to_inspect", []),
                     "tests_likely_needed": [], "expected_patch_scope": "small", "stop_conditions": [],
                     "test_commands": [task.test_command]}],
        # Twice: an empty answer is retried once. A failing reference then fails the way a model would.
        "coder": [{"edits": sol.get("edits", []), "create": sol.get("create", []), "delete": [],
                   "rationale": "reference solution", "tests_to_run": []}] * 2,
        # The reference never repairs; it answers every repair request with "no change".
        "repair": [{"edits": [], "create": [], "delete": [], "rationale": "no repair in the reference"}] * 4,
    }


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _db_metrics(run_id: str) -> dict[str, Any]:
    with get_db() as conn:
        calls = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)),0) tok, "
            "COALESCE(SUM(duration_ms),0) ms, SUM(CASE WHEN degraded IS NOT NULL THEN 1 ELSE 0 END) deg "
            "FROM model_calls WHERE run_id = ?", (run_id,)).fetchone()
        ev = {r["type"]: r["c"] for r in conn.execute(
            "SELECT type, COUNT(*) c FROM run_events WHERE run_id = ? GROUP BY type", (run_id,))}
        err = conn.execute("SELECT message FROM run_events WHERE run_id = ? AND type = 'phase_failed' ORDER BY id DESC LIMIT 1",
                           (run_id,)).fetchone()
    return {"calls": calls["n"], "tokens": calls["tok"], "ms": calls["ms"], "degraded": calls["deg"] or 0,
            "repairs": ev.get("repair_started", 0), "approvals": ev.get("approval_requested", 0),
            "error": err["message"] if err else None}


def _diff_stats(diff: str) -> tuple[int, int, int]:
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    files = sum(1 for ln in diff.splitlines() if ln.startswith("+++ "))
    return added, removed, files


async def run_task(task: EvalTask, svc: TaskService, workdir: Path, *, provider: str, model: str | None,
                   base_url: str | None, time_limit: float) -> TaskResult:
    repo = workdir / task.id
    _materialize(repo, task.files)
    before = {p: _sha(repo / p) for p in task.forbid_changes}

    if provider == "scripted":
        model = f"eval-{task.id}-{uuid.uuid4().hex[:6]}"
        ScriptedProvider.register(model, _script_for(task))

    result = TaskResult(id=task.id, category=task.category, difficulty=task.difficulty)
    started = time.monotonic()
    run = svc.create_run(repo_path=str(repo), task=task.task, provider=provider, model=model, base_url=base_url)
    result.run_id = run["id"]
    try:
        final = await asyncio.wait_for(svc.run_to_completion(run["id"]), timeout=time_limit)
    except TimeoutError:
        svc.cancel(run["id"])
        await asyncio.sleep(0.2)
        final = svc.get_run(run["id"])
        final["status"] = "cancelled"
    result.wall_s = round(time.monotonic() - started, 2)
    result.run_status, result.outcome, result.verdict = final["status"], final.get("outcome"), final.get("verdict")

    m = _db_metrics(run["id"])
    result.model_calls, result.tokens, result.model_ms = m["calls"], m["tokens"], m["ms"]
    result.degraded_calls, result.repair_rounds, result.approvals_requested = m["degraded"], m["repairs"], m["approvals"]
    result.error = m["error"]
    result.lines_added, result.lines_removed, result.files_touched = _diff_stats(svc.diff(run["id"]))

    # Hidden oracle: added only now, so it cannot influence the agent.
    _materialize(repo, task.oracle_files)
    oracle = run_argv(shlex.split(task.oracle_command), str(repo), timeout=ORACLE_TIMEOUT, env=scrubbed_env())
    result.oracle_passed = bool(oracle["success"])
    result.forbidden_change = any(_sha(repo / p) != digest for p, digest in before.items())
    result.status, result.failure_reason = classify(result)
    return result


def _environment(provider: str, model: str | None, base_url: str | None) -> dict[str, Any]:
    import patchquest

    sha = None
    try:
        argv = [shutil.which("git") or "git", "rev-parse", "--short", "HEAD"]
        sha = subprocess.run(  # noqa: S603 - fixed argv, no external input
            argv, capture_output=True, text=True, timeout=5, cwd=Path(patchquest.__file__).parent,
        ).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    agent = get_config().agent
    return {"patchquest_git_sha": sha, "python": sys.version.split()[0], "platform": platform.platform(),
            "provider": provider, "model": model, "base_url": base_url, "agent_config": agent.model_dump(),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


async def run_eval(*, corpus: str | Path | None = None, only: str | None = None, provider: str = "scripted",
                   model: str | None = None, base_url: str | None = None, time_limit: float = 300,
                   progress=None) -> EvalReport:
    tasks = load_corpus(corpus, only)
    config = get_config()
    saved_timeout = config.safety.approval_timeout_seconds
    config.safety.approval_timeout_seconds = 0  # unattended: approvals are denied, never waited on
    from patchquest.database import get_db_path

    previous_db = get_db_path()
    with tempfile.TemporaryDirectory(prefix="pq-eval-") as tmp:
        set_db_path(Path(tmp) / "eval.db")  # eval runs never touch the user's run history
        init_db()
        svc = TaskService()
        results: list[TaskResult] = []
        try:
            for task in tasks:
                r = await run_task(task, svc, Path(tmp) / "repos", provider=provider, model=model,
                                   base_url=base_url, time_limit=time_limit)
                results.append(r)
                if progress:
                    progress(r)
        finally:
            config.safety.approval_timeout_seconds = saved_timeout
            set_db_path(previous_db)
    report = EvalReport(schema=RESULT_SCHEMA, environment=_environment(provider, model, base_url),
                        corpus_digest=corpus_digest(corpus))
    report.tasks = [r.to_dict() for r in results]
    report.summary = summarize(results)
    return report

"""TaskService: create, run, control, approve and inspect runs.

This is the single place run behaviour lives. The HTTP routes, the scheduler and the CLI are
thin adapters over it, so a run behaves the same however it was started. It returns plain
dicts (no HTTP or ORM types) so any interface can serialise them.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from patchquest.database import get_db, insert_event, now_iso
from patchquest.orchestrator.event_bus import event_bus
from patchquest.orchestrator.state_machine import RunStateMachine
from patchquest.security import validate_base_url, validate_repo_path

TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_interrupted"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


class RunNotFound(LookupError):
    pass


class RunNotActive(RuntimeError):
    pass


def _row(r: Any) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


class TaskService:
    def __init__(self) -> None:
        self._machines: dict[str, RunStateMachine] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ create
    def create_run(
        self,
        *,
        repo_path: str,
        task: str,
        provider: str = "mock",
        model: str | None = None,
        runtime_mode: str = "local",
        model_profile: str | None = None,
        memory_mode: str = "repo",
        allow_network: bool = False,
        dry_run: bool = False,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Validate and persist a run in state ``created``. Raises RepoPathError on a bad path."""
        repo_path = validate_repo_path(repo_path)
        base_url = validate_base_url(base_url)
        run_id = str(uuid.uuid4())
        now = now_iso()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO runs (id, repo_path, task, status, provider, model, model_profile,
                   memory_mode, runtime_mode, allow_network, dry_run, base_url, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, repo_path, task, "created", provider, model, model_profile, memory_mode,
                 runtime_mode, int(allow_network), int(dry_run), base_url, now, now),
            )
            insert_event(conn, run_id, "run_created", message=f"Run created: {task[:100]}")
        return self.get_run(run_id)

    def _machine_for(self, run: dict[str, Any]) -> RunStateMachine:
        return RunStateMachine(
            run["id"], run["repo_path"], run["task"], provider=run["provider"] or "mock", model=run["model"],
            runtime_mode=run["runtime_mode"] or "local", dry_run=bool(run["dry_run"]), base_url=run.get("base_url"),
        )

    # --------------------------------------------------------------------- run
    def launch(self, run_id: str) -> None:
        """Start the run in the background of the current event loop."""
        machine = self._machine_for(self.get_run(run_id))
        self._machines[run_id] = machine
        task = asyncio.get_running_loop().create_task(machine.execute())
        self._tasks[run_id] = task  # strong reference: a bare create_task() may be GC'd mid-run

        def _cleanup(_t: asyncio.Task) -> None:
            self._machines.pop(run_id, None)
            self._tasks.pop(run_id, None)

        task.add_done_callback(_cleanup)

    async def run_to_completion(self, run_id: str) -> dict[str, Any]:
        self.launch(run_id)
        await self._tasks[run_id]
        return self.get_run(run_id)

    def cancel(self, run_id: str) -> None:
        machine = self._machines.get(run_id)
        if machine is None:
            raise RunNotActive(run_id)
        machine.cancel()

    async def shutdown(self) -> None:
        for machine in list(self._machines.values()):
            machine.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def is_active(self, run_id: str) -> bool:
        return run_id in self._machines

    # ---------------------------------------------------------------- approvals
    async def approve(self, run_id: str, approval_id: str, approved: bool, note: str | None = None) -> None:
        with get_db() as conn:
            conn.execute(
                "UPDATE approvals SET status = ?, note = ?, resolved_at = ? WHERE id = ? AND run_id = ? AND status = 'pending'",
                ("approved" if approved else "rejected", note, now_iso(), approval_id, run_id),
            )
            insert_event(conn, run_id, "permission_approved" if approved else "permission_rejected",
                         message=f"Approval {approval_id}: {approved}")
        machine = self._machines.get(run_id)
        if machine:
            await machine.resolve_approval(approval_id, approved)

    def pending_approvals(self, run_id: str) -> list[dict[str, Any]]:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM approvals WHERE run_id = ? AND status = 'pending' ORDER BY created_at",
                                (run_id,)).fetchall()
        return [_row(r) for r in rows]

    # ------------------------------------------------------------------ inspect
    def get_run(self, run_id: str) -> dict[str, Any]:
        with get_db() as conn:
            r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            raise RunNotFound(run_id)
        return _row(r)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]

    def events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id",
                                (run_id, after_id)).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            d["payload"] = json.loads(d.pop("payload_json")) if d.get("payload_json") else None
            out.append(d)
        return out

    def report(self, run_id: str) -> dict[str, Any] | None:
        with get_db() as conn:
            r = conn.execute("SELECT * FROM reports WHERE run_id = ?", (run_id,)).fetchone()
        return _row(r) if r else None

    def diff(self, run_id: str) -> str:
        report = self.report(run_id)
        return (report or {}).get("diff_patch") or ""

    async def stream(self, run_id: str, after_id: int = 0, heartbeat: float = 15.0) -> AsyncIterator[dict[str, Any]]:
        """Replay persisted events, then follow live ones; ends after the run's terminal event.

        Subscribing *before* replaying closes the gap in which an event could be missed, and ids
        de-duplicate the overlap. A client that connects after the run finished still receives
        the complete history and the stream ends.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        event_bus.subscribe(run_id, queue)
        try:
            last = after_id
            for event in self.events(run_id, after_id):
                last = event["id"]
                yield event
                if event["type"] in TERMINAL_EVENTS:
                    return
            if self.get_run(run_id)["status"] in TERMINAL_STATUSES:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield {"type": "ping", "run_id": run_id}
                    if not self.is_active(run_id) and self.get_run(run_id)["status"] in TERMINAL_STATUSES:
                        return
                    continue
                if event.get("id", 0) <= last:
                    continue
                last = event["id"]
                yield event
                if event["type"] in TERMINAL_EVENTS:
                    return
        finally:
            event_bus.unsubscribe(run_id, queue)


_service: TaskService | None = None


def get_service() -> TaskService:
    global _service
    if _service is None:
        _service = TaskService()
    return _service

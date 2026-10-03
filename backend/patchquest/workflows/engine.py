"""The durable workflow engine.

Every state change is a database row (``workflow_steps``), so ``advance(run)`` may be called again after any
crash, restart or redeploy and continues exactly where the run was. Waiting (a child agent run, a human, an
external event, a timer) costs nothing: no task, thread or worker is held; ``tick()`` and ``deliver_event()``
wake what is due.

Rules the engine keeps:
* A node runs at most ``max_visits`` times per run; a loop that runs out of visits fails the run loudly.
* An action carries an idempotency key (``run:node:visit``) recorded *before* it is performed. After a crash
  the engine asks the connector whether the effect already happened (``find_existing``) before it ever
  repeats it, and a non-idempotent action it cannot reconcile stops for a person (``uncertain``).
* An action that writes outside the sandbox needs a human approval upstream; this is validated when the
  workflow is saved *and checked again here*, so editing a definition can never bypass the gate.
* One external event never starts the same workflow twice (``trigger_key`` is unique per workflow).
* ``advance`` for one run is serialised by a lock; steps within a run execute one at a time in id order.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from patchquest import database
from patchquest.application.service import TaskService
from patchquest.config import validate_overrides
from patchquest.database import get_db
from patchquest.domain.failures import Origin, PatchQuestError, classify
from patchquest.domain.policy import Result
from patchquest.domain.workflows import (
    REQUIRES_APPROVAL_EFFECTS,
    ActionInfo,
    Node,
    NodeType,
    TemplateError,
    ValidationPolicy,
    Workflow,
    cycle_nodes,
    evaluate,
    matches,
    render,
    validate,
)
from patchquest.persistence import identity as ids
from patchquest.persistence.ledger import now_iso
from patchquest.runtime import policy as policy_runtime
from patchquest.runtime import run_memory
from patchquest.runtime.retry import run_with_retry
from patchquest.workflows import store
from patchquest.workflows.catalog import acting_in

ACTIVE_STEP = ("pending", "running", "waiting")
TERMINAL_RUN = ("completed", "failed", "cancelled")
AGENT_TERMINAL = ("completed", "failed", "cancelled")


class ActionRunner(Protocol):
    """What the engine needs from the connector layer."""

    def info(self, name: str) -> ActionInfo | None: ...

    async def perform(self, name: str, params: dict[str, Any], *, idempotency_key: str, approved_by: str | None) -> dict[str, Any]: ...

    async def find_existing(self, name: str, idempotency_key: str) -> dict[str, Any] | None: ...


class NoActions:
    """No connectors configured: workflows may still use agents, conditions, approvals, waits and timers."""

    def info(self, name: str) -> ActionInfo | None:
        return None

    async def perform(self, name: str, params: dict[str, Any], *, idempotency_key: str, approved_by: str | None) -> dict[str, Any]:
        raise LookupError(f"unknown action '{name}'")

    async def find_existing(self, name: str, idempotency_key: str) -> dict[str, Any] | None:
        return None


@dataclass(frozen=True)
class TriggerEvent:
    source: str
    type: str
    external_id: str
    workspace_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "external"

    @classmethod
    def from_envelope(cls, envelope: Any) -> TriggerEvent:
        """From a connector ``EventEnvelope``. Workflow triggers are namespaced by source (``github.issues.labeled``)."""
        etype = envelope.type if envelope.type.startswith(f"{envelope.source}.") else f"{envelope.source}.{envelope.type}"
        return cls(envelope.source, etype, envelope.external_id, envelope.workspace_id, dict(envelope.payload), envelope.actor)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "type": self.type, "external_id": self.external_id, "actor": self.actor, "payload": self.payload}


class WorkflowError(RuntimeError):
    """A request the engine refuses (invalid definition, missing variable, wrong state); the message is safe to show."""


class WorkflowEngine:
    def __init__(self, service: TaskService, actions: ActionRunner | None = None, *,
                 clock: Callable[[], datetime] | None = None, allow_unapproved_writes: bool = False) -> None:
        self.service = service
        self.actions: ActionRunner = actions or NoActions()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._allow_unapproved_writes = allow_unapproved_writes
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ starting
    def _policy(self) -> ValidationPolicy:
        known = {}
        for name in getattr(self.actions, "names", lambda: [])():
            if (info := self.actions.info(name)) is not None:
                known[name] = info
        return ValidationPolicy(allow_unapproved_writes=self._allow_unapproved_writes, known_actions=known)

    def check(self, wf: Workflow) -> list[Any]:
        """Validation problems under the engine's current actions and policy (empty: safe to save and run)."""
        return validate(wf, self._policy())

    def start(self, workflow_id: str, trigger: dict[str, Any], *, variables: dict[str, Any] | None = None,
              trigger_key: str | None = None, created_by: str = "manual") -> str | None:
        """Create a run of ``workflow_id``. Returns its id, or None when ``trigger_key`` was already used (duplicate event)."""
        with get_db() as conn:
            wf, meta = store.load(conn, workflow_id)
            problems = self.check(wf)  # policy is evaluated at run time too, not only when the workflow was saved
            if problems:
                raise WorkflowError("this workflow no longer passes validation: " + "; ".join(p.message for p in problems))
            merged, missing = {}, []
            for name, spec in wf.variables.items():
                if variables and name in variables:
                    merged[name] = variables[name]
                elif "default" in spec:
                    merged[name] = spec["default"]
                elif spec.get("required"):
                    missing.append(name)
            unknown = set(variables or {}) - set(wf.variables)
            if missing or unknown:
                raise WorkflowError(("missing variable(s): " + ", ".join(missing) if missing else "")
                                    + ("unknown variable(s): " + ", ".join(sorted(unknown)) if unknown else ""))
            run_id = f"wfr_{uuid.uuid4().hex[:16]}"
            now = now_iso()
            try:
                conn.execute("INSERT INTO workflow_runs (id, workflow_id, workspace_id, status, trigger_json, trigger_key, vars_json, "
                             "created_by, created_at, updated_at) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)",
                             (run_id, workflow_id, meta["workspace_id"], json.dumps(trigger, default=str), trigger_key,
                              json.dumps(merged, default=str), created_by, now, now))
            except database.INTEGRITY_ERRORS:
                return None  # the same external event again
            for entry in wf.entries():
                self._new_step(conn, run_id, entry.id, 1)
            store.event(conn, run_id, "workflow_started", actor=created_by, payload={"workflow": wf.name, "version": meta["version"],
                                                                                 "trigger": trigger.get("type")})
        return run_id

    async def deliver_event(self, ev: TriggerEvent) -> list[str]:
        """Route an external event: wake runs waiting for it and start workflows whose trigger matches. Returns run ids touched."""
        touched: list[str] = []
        data = ev.as_dict()
        with get_db() as conn:
            waiting = conn.execute(
                "SELECT s.id, s.workflow_run_id, s.node_id, s.output_json FROM workflow_steps s JOIN workflow_runs r "
                "ON r.id = s.workflow_run_id WHERE s.status = 'waiting' AND s.wait_kind = 'event' AND s.wait_key = ? "
                "AND r.workspace_id = ? ORDER BY s.id", (ev.type, ev.workspace_id)).fetchall()
            for row in waiting:
                flt = (json.loads(row["output_json"]) if row["output_json"] else {}).get("filter") or {}
                if not matches(flt, data):
                    continue
                conn.execute("UPDATE workflow_steps SET status = 'succeeded', output_json = ?, finished_at = ? "
                             "WHERE id = ? AND status = 'waiting'", (json.dumps(data, default=str), now_iso(), row["id"]))
                store.event(conn, row["workflow_run_id"], "step_woken", node_id=row["node_id"], actor=ev.actor,
                            payload={"event": ev.type, "external_id": ev.external_id})
                self._fire_after_success(conn, row["workflow_run_id"], row["node_id"], {None})
                touched.append(row["workflow_run_id"])
            candidates = store.active_for_trigger(conn, ev.type, ev.workspace_id)
        for workflow_id in candidates:
            with get_db() as conn:
                wf, _ = store.load(conn, workflow_id)
            if not matches(wf.trigger.filter, data):
                continue
            try:
                run_id = self.start(workflow_id, data, trigger_key=f"{ev.source}:{ev.external_id}", created_by=f"event:{ev.source}")
            except WorkflowError as exc:  # one broken workflow must not stop the event reaching the others
                with get_db() as conn:
                    ids.audit(conn, "workflow.start_failed", actor=f"event:{ev.source}", outcome="refused", workspace_id=ev.workspace_id,
                              target=workflow_id, detail={"event": ev.type, "reason": str(exc)[:500]})
                continue
            if run_id:
                touched.append(run_id)
        for run_id in dict.fromkeys(touched):
            await self.advance(run_id)
        return list(dict.fromkeys(touched))

    # ------------------------------------------------------------------ human decisions
    async def decide(self, run_id: str, node_id: str, decision: str, actor: str) -> None:
        """Answer an approval step: ``approve`` or ``deny``. The first answer wins."""
        if decision not in ("approve", "deny"):
            raise WorkflowError("decision must be 'approve' or 'deny'")
        with get_db() as conn:
            cur = conn.execute("UPDATE workflow_steps SET status = 'succeeded', decision = ?, decided_by = ?, finished_at = ?, "
                               "output_json = ? WHERE workflow_run_id = ? AND node_id = ? AND status = 'waiting' AND wait_kind = 'approval'",
                               ("approved" if decision == "approve" else "denied", actor, now_iso(),
                                json.dumps({"decision": "approved" if decision == "approve" else "denied", "decided_by": actor}),
                                run_id, node_id))
            if cur.rowcount != 1:
                raise WorkflowError("there is no pending approval for that step (already decided, expired or not waiting)")
            store.event(conn, run_id, "approval_decided", node_id=node_id, actor=actor, message=decision)
            self._fire_after_success(conn, run_id, node_id, {None, "approved"} if decision == "approve" else {"denied"},
                                     approval_denied=decision == "deny")
        await self.advance(run_id)

    async def cancel(self, run_id: str, actor: str) -> None:
        async with self._lock(run_id):
            with get_db() as conn:
                run = store.get_run(conn, run_id)
                if run["status"] in TERMINAL_RUN:
                    return
                children = [r["child_run_id"] for r in conn.execute(
                    "SELECT child_run_id FROM workflow_steps WHERE workflow_run_id = ? AND child_run_id IS NOT NULL "
                    "AND status IN ('waiting', 'running')", (run_id,))]
                conn.execute("UPDATE workflow_steps SET status = 'skipped', finished_at = ? WHERE workflow_run_id = ? "
                             "AND status IN ('pending', 'running', 'waiting')", (now_iso(), run_id))
                self._finish(conn, run_id, "cancelled", None)
                store.event(conn, run_id, "workflow_cancelled", actor=actor)
            for child in children:
                try:
                    self.service.cancel(child)
                except Exception:  # noqa: S110 - the child may already have finished; cancelling is best effort
                    pass

    async def resolve(self, run_id: str, node_id: str, outcome: str, actor: str) -> None:
        """A person settles an ``uncertain`` action: it ``happened``, ``retry`` it, or treat it as ``failed``."""
        if outcome not in ("happened", "retry", "failed"):
            raise WorkflowError("outcome must be 'happened', 'retry' or 'failed'")
        async with self._lock(run_id):
            with get_db() as conn:
                row = conn.execute("SELECT id FROM workflow_steps WHERE workflow_run_id = ? AND node_id = ? AND status = 'uncertain'",
                                   (run_id, node_id)).fetchone()
                if row is None:
                    raise WorkflowError("that step is not waiting for a decision about whether it happened")
                new = {"happened": "succeeded", "retry": "pending", "failed": "failed"}[outcome]
                conn.execute("UPDATE workflow_steps SET status = ?, decided_by = ?, finished_at = CASE WHEN ? = 'pending' THEN NULL ELSE ? END "
                             "WHERE id = ?", (new, actor, new, now_iso(), row["id"]))
                store.event(conn, run_id, "step_resolved", node_id=node_id, actor=actor, message=outcome)
                if outcome == "happened":
                    self._fire_after_success(conn, run_id, node_id, {None})
        await self.advance(run_id)

    # ------------------------------------------------------------------ progress
    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    async def tick(self) -> int:
        """Advance every unfinished run: wakes due timers and timeouts, polls child agent runs, finishes work."""
        with get_db() as conn:
            run_ids = [r["id"] for r in conn.execute("SELECT id FROM workflow_runs WHERE status IN ('running', 'waiting')")]
        for run_id in run_ids:
            await self.advance(run_id)
        return len(run_ids)

    async def advance(self, run_id: str) -> None:
        async with self._lock(run_id):
            for _ in range(1000):  # a run cannot loop forever: visits are capped, this guards a bug, not a workflow
                with get_db() as conn:
                    run = store.get_run(conn, run_id)
                    if run["status"] in TERMINAL_RUN:
                        return
                    wf, _ = store.load(conn, run["workflow_id"])
                    pending = [dict(r) for r in conn.execute(
                        "SELECT * FROM workflow_steps WHERE workflow_run_id = ? AND status IN ('pending', 'running', 'waiting') "
                        "ORDER BY id", (run_id,))]
                progressed = False
                for step in pending:
                    progressed |= await self._step(run, wf, step)
                    with get_db() as conn:
                        if store.get_run(conn, run_id)["status"] in TERMINAL_RUN:
                            return
                if not progressed:
                    break
            with get_db() as conn:
                self._settle(conn, run_id)

    def _settle(self, conn: sqlite3.Connection, run_id: str) -> None:
        if store.get_run(conn, run_id)["status"] in TERMINAL_RUN:
            return
        rows = conn.execute("SELECT status FROM workflow_steps WHERE workflow_run_id = ?", (run_id,)).fetchall()
        statuses = {r["status"] for r in rows}
        if statuses & {"pending", "running"}:
            new = "running"
        elif statuses & {"waiting", "uncertain"}:
            new = "waiting"
        else:
            self._finish(conn, run_id, "completed", None)
            store.event(conn, run_id, "workflow_completed")
            return
        conn.execute("UPDATE workflow_runs SET status = ?, updated_at = ? WHERE id = ?", (new, now_iso(), run_id))

    def _finish(self, conn: sqlite3.Connection, run_id: str, status: str, error: str | None) -> None:
        conn.execute("UPDATE workflow_runs SET status = ?, error = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                     (status, error, now_iso(), now_iso(), run_id))

    def _fail_run(self, conn: sqlite3.Connection, run_id: str, node_id: str | None, error: str) -> None:
        conn.execute("UPDATE workflow_steps SET status = 'skipped', finished_at = ? WHERE workflow_run_id = ? "
                     "AND status IN ('pending', 'running', 'waiting')", (now_iso(), run_id))
        self._finish(conn, run_id, "failed", error)
        store.event(conn, run_id, "workflow_failed", node_id=node_id, message=error)

    # ------------------------------------------------------------------ one step
    def _new_step(self, conn: sqlite3.Connection, run_id: str, node_id: str, visit: int) -> None:
        conn.execute("INSERT INTO workflow_steps (workflow_run_id, node_id, visit, status, idempotency_key) VALUES (?, ?, ?, 'pending', ?)",
                     (run_id, node_id, visit, f"{run_id}:{node_id}:{visit}"))

    def _context(self, conn: sqlite3.Connection, run: dict[str, Any]) -> dict[str, Any]:
        nodes: dict[str, Any] = {}
        for row in conn.execute("SELECT node_id, output_json FROM workflow_steps WHERE workflow_run_id = ? AND status = 'succeeded' "
                                "ORDER BY id", (run["id"],)):
            nodes[row["node_id"]] = {"output": json.loads(row["output_json"]) if row["output_json"] else {}}
        return {"trigger": run["trigger"], "vars": run["vars"], "nodes": nodes}

    async def _step(self, run: dict[str, Any], wf: Workflow, step: dict[str, Any]) -> bool:
        """Move one step as far as it can go right now. True if anything changed."""
        node = wf.node(step["node_id"])
        handler = {
            NodeType.AGENT: self._agent, NodeType.ACTION: self._action, NodeType.CONDITION: self._condition,
            NodeType.APPROVAL: self._approval, NodeType.WAIT_EVENT: self._wait_event, NodeType.TIMER: self._timer,
            NodeType.END: self._end,
        }[node.type]
        try:
            return await handler(run, wf, node, step)
        except TemplateError as exc:
            self._step_failed(run, wf, node, step, f"{exc}")
            return True

    def _step_failed(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any], error: str,
                     output: dict[str, Any] | None = None) -> None:
        with get_db() as conn:
            conn.execute("UPDATE workflow_steps SET status = 'failed', error = ?, output_json = ?, finished_at = ? WHERE id = ?",
                         (error[:2000], json.dumps(output, default=str) if output is not None else None, now_iso(), step["id"]))
            store.event(conn, run["id"], "step_failed", node_id=node.id, message=error[:500])
            handles = node.config.get("on_failure") == "continue" and any(e.when == "failed" for e in wf.outgoing(node.id))
            if handles:
                self._fire(conn, wf, run["id"], node, lambda e: e.when == "failed")
            else:
                self._fail_run(conn, run["id"], node.id, f"{node.id}: {error}"[:2000])

    def _succeed(self, run_id: str, step_id: int, output: dict[str, Any] | None, node_id: str) -> None:
        with get_db() as conn:
            conn.execute("UPDATE workflow_steps SET status = 'succeeded', output_json = ?, finished_at = ? WHERE id = ?",
                         (json.dumps(output, default=str) if output is not None else None, now_iso(), step_id))
            store.event(conn, run_id, "step_succeeded", node_id=node_id)

    def _fire_after_success(self, conn: sqlite3.Connection, run_id: str, node_id: str, labels: set[str | None],
                            approval_denied: bool = False) -> None:
        run = store.get_run(conn, run_id)
        wf, _ = store.load(conn, run["workflow_id"])
        node = wf.node(node_id)
        fired = self._fire(conn, wf, run_id, node, lambda e: e.when in labels)
        if approval_denied and not fired:
            self._fail_run(conn, run_id, node_id, f"{node_id}: the approval was denied")

    def _fire(self, conn: sqlite3.Connection, wf: Workflow, run_id: str, node: Node, pick: Callable[[Any], bool]) -> int:
        looping = cycle_nodes(wf)
        count = 0
        for edge in wf.outgoing(node.id):
            if not pick(edge):
                continue
            target = wf.node(edge.target)
            rows = conn.execute("SELECT visit, status FROM workflow_steps WHERE workflow_run_id = ? AND node_id = ? ORDER BY visit",
                                (run_id, target.id)).fetchall()
            if any(r["status"] in ACTIVE_STEP or r["status"] == "uncertain" for r in rows):
                continue  # already on its way (a second branch arrived at a join)
            visit = (rows[-1]["visit"] if rows else 0) + 1
            if visit > target.max_visits:
                if target.id in looping:
                    self._fail_run(conn, run_id, target.id, f"{target.id}: gave up after {target.max_visits} attempts")
                    return count
                continue  # a join reached again: it has already run
            self._new_step(conn, run_id, target.id, visit)
            count += 1
        return count

    # ---- node types
    async def _condition(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any]) -> bool:
        with get_db() as conn:
            ctx = self._context(conn, run)
        result = evaluate(node.config["if"], ctx)
        with get_db() as conn:
            conn.execute("UPDATE workflow_steps SET status = 'succeeded', output_json = ?, started_at = ?, finished_at = ? WHERE id = ?",
                         (json.dumps({"result": result}), now_iso(), now_iso(), step["id"]))
            store.event(conn, run["id"], "step_succeeded", node_id=node.id, message=str(result).lower())
            self._fire(conn, wf, run["id"], node, lambda e: e.when == ("true" if result else "false"))
        return True

    async def _approval(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any]) -> bool:
        if step["status"] == "pending":
            with get_db() as conn:
                ctx = self._context(conn, run)
            message = render(node.config.get("message", f"Approve step {node.id}?"), ctx)
            timeout = node.config.get("timeout_s")
            wake = (self._clock() + timedelta(seconds=timeout)).isoformat() if timeout else None
            with get_db() as conn:
                conn.execute("UPDATE workflow_steps SET status = 'waiting', wait_kind = 'approval', wait_key = ?, wake_at = ?, started_at = ? "
                             "WHERE id = ?", (node.id, wake, now_iso(), step["id"]))
                store.event(conn, run["id"], "approval_requested", node_id=node.id, message=str(message))
            return True
        if step["wake_at"] and self._clock().isoformat() >= step["wake_at"]:
            with get_db() as conn:
                cur = conn.execute("UPDATE workflow_steps SET status = 'succeeded', decision = 'timeout', finished_at = ?, output_json = ? "
                                   "WHERE id = ? AND status = 'waiting'", (now_iso(), json.dumps({"decision": "timeout"}), step["id"]))
                if cur.rowcount:
                    store.event(conn, run["id"], "approval_expired", node_id=node.id)
                    has_timeout = any(e.when == "timeout" for e in wf.outgoing(node.id))
                    fired = self._fire(conn, wf, run["id"], node, lambda e: e.when == ("timeout" if has_timeout else "denied"))
                    if not fired:
                        self._fail_run(conn, run["id"], node.id, f"{node.id}: no one approved in time")
            return True
        return False

    async def _timer(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any]) -> bool:
        if step["status"] == "pending":
            wake = (self._clock() + timedelta(seconds=float(node.config["seconds"]))).isoformat()
            with get_db() as conn:
                conn.execute("UPDATE workflow_steps SET status = 'waiting', wait_kind = 'timer', wake_at = ?, started_at = ? WHERE id = ?",
                             (wake, now_iso(), step["id"]))
                store.event(conn, run["id"], "step_waiting", node_id=node.id, message=f"until {wake}")
            return True
        if self._clock().isoformat() >= step["wake_at"]:
            with get_db() as conn:
                conn.execute("UPDATE workflow_steps SET status = 'succeeded', finished_at = ? WHERE id = ?", (now_iso(), step["id"]))
                store.event(conn, run["id"], "step_woken", node_id=node.id, actor="timer")
                self._fire(conn, wf, run["id"], node, lambda e: e.when is None)
            return True
        return False

    async def _wait_event(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any]) -> bool:
        if step["status"] == "pending":
            with get_db() as conn:
                ctx = self._context(conn, run)
            event_type = str(render(node.config["event"], ctx))
            flt = render(node.config.get("filter") or {}, ctx)
            timeout = node.config.get("timeout_s")
            wake = (self._clock() + timedelta(seconds=timeout)).isoformat() if timeout else None
            with get_db() as conn:
                conn.execute("UPDATE workflow_steps SET status = 'waiting', wait_kind = 'event', wait_key = ?, wake_at = ?, "
                             "output_json = ?, started_at = ? WHERE id = ?",
                             (event_type, wake, json.dumps({"filter": flt}), now_iso(), step["id"]))
                store.event(conn, run["id"], "step_waiting", node_id=node.id, message=f"for {event_type}")
            return True
        if step["wake_at"] and self._clock().isoformat() >= step["wake_at"]:
            with get_db() as conn:
                conn.execute("UPDATE workflow_steps SET status = 'failed', error = 'timed out waiting for the event', finished_at = ? "
                             "WHERE id = ? AND status = 'waiting'", (now_iso(), step["id"]))
                store.event(conn, run["id"], "step_timeout", node_id=node.id)
                if any(e.when == "timeout" for e in wf.outgoing(node.id)):
                    self._fire(conn, wf, run["id"], node, lambda e: e.when == "timeout")
                else:
                    self._fail_run(conn, run["id"], node.id, f"{node.id}: timed out waiting for {step['wait_key']}")
            return True
        return False

    async def _end(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any]) -> bool:
        result = node.config.get("result", "success")
        children = []
        with get_db() as conn:
            conn.execute("UPDATE workflow_steps SET status = 'succeeded', finished_at = ?, started_at = ? WHERE id = ?",
                         (now_iso(), now_iso(), step["id"]))
            children = [r["child_run_id"] for r in conn.execute(
                "SELECT child_run_id FROM workflow_steps WHERE workflow_run_id = ? AND child_run_id IS NOT NULL AND status = 'waiting'",
                (run["id"],))]
            conn.execute("UPDATE workflow_steps SET status = 'skipped', finished_at = ? WHERE workflow_run_id = ? "
                         "AND status IN ('pending', 'running', 'waiting') AND id != ?", (now_iso(), run["id"], step["id"]))
            if result == "failure":
                self._finish(conn, run["id"], "failed", f"{node.id}: the workflow ended in failure")
                store.event(conn, run["id"], "workflow_failed", node_id=node.id, message="ended in failure")
            else:
                self._finish(conn, run["id"], "completed", None)
                store.event(conn, run["id"], "workflow_completed", node_id=node.id)
        for child in children:
            try:
                self.service.cancel(child)
            except Exception:  # noqa: S110 - already finished
                pass
        return True

    async def _agent(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any]) -> bool:
        key = step["idempotency_key"]
        if step["status"] == "pending":
            with get_db() as conn:
                ctx = self._context(conn, run)
            cfg = render(dict(node.config), ctx)
            with get_db() as conn:  # intent first: after a crash the run is found again through its created_by marker
                conn.execute("UPDATE workflow_steps SET status = 'running', started_at = ? WHERE id = ?", (now_iso(), step["id"]))
            try:
                child = self.service.create_run(
                    repo_path=str(cfg.get("repo") or ctx["vars"].get("repo", "")), task=str(cfg["task"]),
                    provider=cfg.get("provider") or "mock", model=cfg.get("model"), base_url=cfg.get("base_url"),
                    workspace_id=run["workspace_id"], created_by=f"workflow:{key}", acting_as=run.get("created_by"))
            except (ValueError, OSError) as exc:  # includes RepoPathError
                self._step_failed(run, wf, node, step, f"could not start the agent: {exc}")
                return True
            if cfg.get("overrides"):
                try:
                    overrides = validate_overrides(dict(cfg["overrides"]))
                except ValueError as exc:
                    self._step_failed(run, wf, node, step, f"invalid agent overrides: {exc}")
                    return True
                with get_db() as conn:
                    conn.execute("UPDATE runs SET overrides_json = ? WHERE id = ?", (json.dumps(overrides), child["id"]))
            self._link_child(run, node, step, child["id"])
            return True
        if step["status"] == "running":  # crashed between "intent" and "linked": find the run we already created
            with get_db() as conn:
                row = conn.execute("SELECT id FROM runs WHERE created_by = ?", (f"workflow:{key}",)).fetchone()
            if row is None:
                with get_db() as conn:
                    conn.execute("UPDATE workflow_steps SET status = 'pending' WHERE id = ?", (step["id"],))
                return True
            self._link_child(run, node, step, row["id"])
            return True
        # waiting on the child agent run
        child = self.service.get_run(step["child_run_id"])
        if child["status"] == "created" and not self.service.is_active(child["id"]):
            self.service.launch(child["id"])  # the process died after the run was recorded but before it started
            return True
        if child["status"] not in AGENT_TERMINAL:
            return False
        output = {"run_id": child["id"], "status": child["status"], "outcome": child.get("outcome"),
                  "verdict": child.get("verdict"), "failure_kind": child.get("failure_kind")}
        if child["status"] == "completed":
            with get_db() as conn:
                conn.execute("UPDATE workflow_steps SET status = 'succeeded', output_json = ?, finished_at = ? WHERE id = ?",
                             (json.dumps(output), now_iso(), step["id"]))
                store.event(conn, run["id"], "step_succeeded", node_id=node.id, payload=output)
                self._fire(conn, wf, run["id"], node, lambda e: e.when is None)
        else:
            self._step_failed(run, wf, node, step, f"the agent run {child['status']}"
                              + (f" ({child['failure_kind']})" if child.get("failure_kind") else ""), output)
        return True

    def _link_child(self, run: dict[str, Any], node: Node, step: dict[str, Any], child_id: str) -> None:
        with get_db() as conn:
            conn.execute("UPDATE workflow_steps SET status = 'waiting', wait_kind = 'child_run', child_run_id = ? WHERE id = ?",
                         (child_id, step["id"]))
            store.event(conn, run["id"], "step_started", node_id=node.id, payload={"run_id": child_id})
        if self.service.get_run(child_id)["status"] == "created" and not self.service.is_active(child_id):
            self.service.launch(child_id)

    async def _action(self, run: dict[str, Any], wf: Workflow, node: Node, step: dict[str, Any]) -> bool:
        name, key = str(node.config["action"]), step["idempotency_key"]
        info = self.actions.info(name)
        if info is None:
            self._step_failed(run, wf, node, step, f"unknown action '{name}'")
            return True
        with get_db() as conn:
            approver = self._approver(conn, run["id"], wf, node.id)
            ctx = self._context(conn, run)
        decision = policy_runtime.decide(
            policy_runtime.chain_for(workspace_id=run["workspace_id"], workflow_id=run["workflow_id"]),
            f"action.{name}", info.side_effect)
        if decision.result is Result.DENY:
            self._step_failed(run, wf, node, step, f"'{name}' is blocked by policy '{decision.source_policy}': {decision.reason}",
                              {"policy": decision.to_dict()})
            return True
        if decision.approval_required and approver is None and not self._allow_unapproved_writes:
            detail: dict[str, Any] = {"policy": decision.to_dict()}
            note = ""
            wants_auto = run_memory.preference_for(run["workspace_id"], run.get("created_by"), "automation.external_writes")
            if wants_auto.value == "auto":
                note = " Your automation preference ('auto') does not override this."
                detail["explanation"] = {"decision": "approval_required", "because": [
                    {"kind": "policy", "policy": decision.source_policy, "scope": decision.scope.name.lower(), "reason": decision.reason}],
                    "overrode_preference": wants_auto.to_dict()}
            self._step_failed(run, wf, node, step, f"'{name}' needs a human approval first ({decision.reason}).{note}", detail)
            return True
        if info.side_effect in REQUIRES_APPROVAL_EFFECTS and approver is None and not self._allow_unapproved_writes:
            self._step_failed(run, wf, node, step, f"'{name}' ({info.side_effect.value}) needs a human approval first")
            return True
        if step["status"] == "running":  # crashed while performing: did it happen?
            with acting_in(run["workspace_id"]):
                existing = await self.actions.find_existing(name, key)
            if existing is not None:
                self._succeed(run["id"], step["id"], existing, node.id)
                with get_db() as conn:
                    self._fire(conn, wf, run["id"], node, lambda e: e.when is None)
                return True
            if not info.idempotent:
                with get_db() as conn:
                    conn.execute("UPDATE workflow_steps SET status = 'uncertain' WHERE id = ?", (step["id"],))
                    store.event(conn, run["id"], "step_uncertain", node_id=node.id,
                                message=f"'{name}' may or may not have happened and cannot be repeated safely")
                return True
        params = render(dict(node.config.get("params") or {}), ctx)
        with get_db() as conn:
            conn.execute("UPDATE workflow_steps SET status = 'running', started_at = COALESCE(started_at, ?) WHERE id = ?",
                         (now_iso(), step["id"]))
            store.event(conn, run["id"], "step_started", node_id=node.id, message=name)

        async def perform() -> dict[str, Any]:
            with acting_in(run["workspace_id"]):
                return await self.actions.perform(name, params, idempotency_key=key, approved_by=approver)

        try:
            output = await run_with_retry(perform, origin=Origin.CONNECTOR, idempotent=info.idempotent)
        except Exception as exc:
            failure = classify(exc, origin=Origin.CONNECTOR)
            # Errors raised by PatchQuest itself carry a message written for people; third-party errors get the generic one.
            said = exc.detail if isinstance(exc, PatchQuestError) else failure.spec.message
            self._step_failed(run, wf, node, step, f"{said} ({failure.kind.value})", {"failure": failure.to_payload()})
            return True
        self._succeed(run["id"], step["id"], output, node.id)
        with get_db() as conn:
            self._fire(conn, wf, run["id"], node, lambda e: e.when is None)
        return True

    def _approver(self, conn: sqlite3.Connection, run_id: str, wf: Workflow, node_id: str) -> str | None:
        """Who approved on the way to this node: the latest approved approval step that is an ancestor of it."""
        ancestors = set()
        stack = [e.source for e in wf.incoming(node_id)]
        while stack:
            cur = stack.pop()
            if cur not in ancestors:
                ancestors.add(cur)
                stack.extend(e.source for e in wf.incoming(cur))
        rows = conn.execute("SELECT node_id, decided_by FROM workflow_steps WHERE workflow_run_id = ? AND decision = 'approved' "
                            "ORDER BY id DESC", (run_id,)).fetchall()
        return next((r["decided_by"] for r in rows if r["node_id"] in ancestors), None)

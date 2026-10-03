"""Build the demo world: repositories, history, integrations, a workflow, and one approval waiting for a person.

Runs execute the real pipeline (shadow workspace, validation, repair, approvals, checkpoints, ledger) with scripted models,
so every event, metric and diff in the demo is genuine; only the model answers and the GitHub/Slack servers are simulated.
It is idempotent: if the workspace already has runs nothing is added.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.application import TaskService, get_service
from patchquest.database import get_db
from patchquest.demo import fixtures as fx
from patchquest.demo import simulators
from patchquest.domain.approvals import Decision
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.memory import MemoryKind, Source
from patchquest.domain.policy import Scope
from patchquest.domain.workflows import parse
from patchquest.integrations import service as integrations
from patchquest.runtime import memory_service as memory
from patchquest.runtime import policy
from patchquest.workflows import store

ACTOR = "demo:seed"
WS = LOCAL_WORKSPACE_ID
EMPTY = {"edits": [], "create": [], "delete": [], "rationale": "no further change"}
REPAIR_REFUND = fx.edit("payments/refunds.py", '    return {"approved": amount_cents < 1_000, "amount": amount_cents}',
                        '    if amount_cents > REFUND_LIMIT_CENTS:\n        return {"approved": False, "reason": "over the refund limit"}\n    return {"approved": True, "amount": amount_cents}',
                        "the limit is REFUND_LIMIT_CENTS, not 1,000")
NPM = "npm test"


def _script(model: str, *, planner: dict[str, Any], coder: list[Any], repair: list[Any] | None = None, extra: dict[str, list[Any]] | None = None) -> None:
    ScriptedProvider.register(model, {"planner": [planner], "coder": coder, "reviewer": [fx.REVIEW_OK],
                                      **({"repair": repair} if repair else {}), **(extra or {})})


def register_scripts() -> None:
    """The model answers behind the seeded runs and the live issue. Registered at every start: scripts live in memory."""
    _script("demo-fees", planner=fx.plan(["payments/fees.py"], "net_amount adds the fee; it must subtract it", fx.unit("fees")), coder=[fx.FIX_NET])
    _script("demo-refund", planner=fx.plan(["payments/refunds.py"], "enforce the refund limit", fx.unit("refunds")), coder=[fx.WRONG_REFUND], repair=[REPAIR_REFUND])
    _script("demo-cart", planner=fx.plan(["src/cart.js"], "total ignores quantity", NPM), coder=[fx.FIX_CART])
    _script("demo-tax-denied", planner=fx.plan(["payments/tax.py"], "round tax to the nearest cent", fx.unit("tax")), coder=[fx.HALF_TAX], repair=[EMPTY, EMPTY])
    _script("demo-tax-pending", planner=fx.plan(["payments/tax.py"], "round tax to the nearest cent", fx.unit("tax")), coder=[fx.HALF_TAX], repair=[EMPTY, EMPTY])
    # a stronger model for the fork demonstration: fork the denied tax run from its plan with this model and the patch validates
    _script("demo-tax-fixed", planner=fx.plan(["payments/tax.py"], "round tax to the nearest cent", fx.unit("tax")), coder=[fx.FIX_TAX])
    _script("demo-explain", planner=fx.plan(["payments/refunds.py"], "read-only"), coder=[EMPTY],
            extra={"analyst": ["Refunds go through payments.refunds.process_refund(amount_cents, original_cents). It rejects a refund larger than "
                               "the original charge with a ValueError and otherwise approves it. REFUND_LIMIT_CENTS (50,000) exists but is not "
                               "yet enforced there - tests/test_refunds.py already expects refunds above it to be declined."]})
    # the live scenario (a labelled GitHub issue) may be triggered several times: a script per trigger
    ScriptedProvider.register("demo-issue", {"planner": [fx.plan(["payments/currency.py"], "to_cents loses a cent on some prices", fx.unit("currency"))] * 8,
                                             "coder": [fx.FIX_PRICE] * 8, "reviewer": [fx.REVIEW_OK] * 8})


async def _finish(svc: TaskService, run_id: str, limit_s: float = 120.0) -> None:
    waited = 0.0
    while svc.is_active(run_id) and waited < limit_s:
        await asyncio.sleep(0.05)
        waited += 0.05


async def _approval_id(run_id: str, limit_s: float = 60.0) -> str:
    waited = 0.0
    while waited < limit_s:
        with get_db() as conn:
            row = conn.execute("SELECT id FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)).fetchone()
        if row:
            return str(row["id"])
        await asyncio.sleep(0.05)
        waited += 0.05
    raise TimeoutError(f"run {run_id} never asked for approval")


def _prepare_world(directory: Path) -> tuple[Path, Path]:
    os.environ.update(simulators.ENV)
    repos = directory / "repos"
    repos.mkdir(parents=True, exist_ok=True)
    payments = repos / "payments-service"
    checkout = repos / "web-checkout"
    if not payments.exists():
        fx.write_repo(payments, fx.PAYMENTS)
    if not checkout.exists():
        fx.write_repo(checkout, fx.CHECKOUT)
    with get_db() as conn:
        conn.execute("UPDATE organizations SET name = 'Acme (demo)' WHERE id = 'org_local'")
        conn.execute("UPDATE workspaces SET name = 'Acme Platform' WHERE id = 'ws_local'")
    return payments, checkout


def _configure(payments: Path, checkout: Path) -> None:
    with get_db() as conn:
        owner = memory.owner_for(conn, WS)
        memory.set_preference(conn, owner, scope=Scope.REPOSITORY, ref=str(checkout), key="test.commands", value=[NPM], actor="demo:ana")
        memory.remember(conn, owner, scope=Scope.REPOSITORY, ref=str(payments), key="refunds.limit", kind=MemoryKind.REPOSITORY,
                        source=Source.USER_EXPLICIT, value="Refunds above REFUND_LIMIT_CENTS must be declined (approved: false), not raised.",
                        reason="stated by the payments team", actor="demo:ana", metadata={"applies_to": ["payments/refunds"]})
        integrations.create(conn, WS, "github", "GitHub (simulator)", {"repo": simulators.REPO},
                            {"token": {"env": "PATCHQUEST_DEMO_GITHUB_TOKEN"}, "webhook_secret": {"env": "PATCHQUEST_DEMO_GITHUB_WEBHOOK"}}, ACTOR)
        integrations.create(conn, WS, "slack", "Slack (simulator)", {"channels": [simulators.CHANNEL]},
                            {"bot_token": {"env": "PATCHQUEST_DEMO_SLACK_TOKEN"}, "signing_secret": {"env": "PATCHQUEST_DEMO_SLACK_SIGNING"}}, ACTOR)
        raw = json.loads(json.dumps(fx.ISSUE_WORKFLOW).replace("{{PAYMENTS_REPO}}", str(payments)))
        store.save_version(conn, WS, parse(raw), "demo:ana")
    policy.store({"name": "release-safety", "scope": "workspace", "rules": [
        {"action": "model.use.openai", "result": "DENY", "reason": "source code stays on local models in this workspace"},
        {"action": "model.use.anthropic", "result": "DENY", "reason": "source code stays on local models in this workspace"}],
        "limits": {"agent.max_model_calls": 30, "agent.max_commands": 40}}, scope_ref=WS, actor="demo:ana")


async def seed(directory: Path, sims: simulators.Simulators) -> dict[str, Any]:
    """Create the demo world in the current database. Returns what was made (and ``{"seeded": False}`` if it already existed)."""
    from patchquest.config import get_config

    register_scripts()
    integrations.set_http(sims.http())
    os.environ.update(simulators.ENV)  # the integrations hold references to these, so every start needs them, not only the first
    get_config().safety.approval_timeout_seconds = 3600  # a waiting approval should still be there when someone looks
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone():
            return {"seeded": False}
    payments, checkout = _prepare_world(directory)
    _configure(payments, checkout)
    svc = get_service()

    def start(repo: Path, task: str, model: str, **kw: Any) -> str:
        run = svc.create_run(repo_path=str(repo), task=task, provider="scripted", model=model, created_by="demo:ana", **kw)
        svc.launch(run["id"])
        return str(run["id"])

    runs: dict[str, str] = {}
    for key, repo, task, model in (
            ("fees", payments, "Fix net_amount: the merchant is credited the amount plus the fee instead of minus it", "demo-fees"),
            ("refund", payments, "Decline refunds above the refund limit in process_refund", "demo-refund"),
            ("cart", checkout, "The cart total ignores each item's quantity", "demo-cart")):
        runs[key] = start(repo, task, model)
        await _finish(svc, runs[key])
    runs["explain"] = start(payments, "Explain how refunds are processed. Do not modify any files.", "demo-explain")
    await _finish(svc, runs["explain"])
    runs["tax_denied"] = start(payments, "Round sales tax to the nearest cent in compute_tax", "demo-tax-denied")
    await svc.decide(runs["tax_denied"], await _approval_id(runs["tax_denied"]), Decision.DENY, actor="demo:ana", note="The tests still fail; not applying this.")
    await _finish(svc, runs["tax_denied"])
    runs["tax_pending"] = start(payments, "Round sales tax to the nearest cent in compute_tax (second attempt)", "demo-tax-pending")
    await _approval_id(runs["tax_pending"])  # left waiting for a person
    return {"seeded": True, "runs": runs, "repos": {"payments": str(payments), "checkout": str(checkout)}}

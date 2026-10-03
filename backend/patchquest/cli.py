"""PatchQuest command line interface.

A thin adapter over :class:`patchquest.application.TaskService`: it runs the same engine the
API runs, in-process, so no server is needed for local use.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from patchquest.database import get_db

EXIT_OK, EXIT_FAILED, EXIT_REJECTED, EXIT_INTERRUPTED, EXIT_NEEDS_CONFIRMATION, EXIT_USAGE = 0, 1, 2, 3, 4, 64
GOOD_OUTCOMES = {"applied", "no_changes", "read_only"}
ICON = {"ok": "✓", "info": "·", "warn": "!", "fail": "✗"}


def _bootstrap(config: str | None) -> None:
    from patchquest.config import load_config, set_config
    from patchquest.database import init_db

    if config:
        os.environ["PATCHQUEST_CONFIG"] = config
    set_config(load_config(config))
    init_db()


def _emit_json(obj: Any) -> None:
    print(json.dumps(obj, default=str), flush=True)


def _fmt_event(e: dict) -> str | None:
    t, phase, msg = e["type"], e.get("phase"), e.get("message") or ""
    if t == "phase_started":
        return f"▶ {phase}"
    if t in ("phase_completed",):
        return None
    if t == "phase_skipped":
        return f"  - skipped {phase}: {msg}"
    if t in ("phase_failed", "phase_blocked"):
        return f"  ✗ {phase}: {msg}"
    if t in ("command_executed", "command_blocked", "command_denied", "tests_completed", "repair_started",
             "patch_staged", "patch_applied", "patch_rejected", "approval_requested", "approval_expired",
             "baseline_started", "context_selected", "run_failed", "run_completed", "workspace_created"):
        return f"  {t.replace('_', ' ')}: {msg}"
    return None


async def _approve_interactively(svc, run_id: str, event: dict, no_input: bool) -> None:
    from patchquest.domain.approvals import ApprovalError, Decision

    payload = event.get("payload") or {}
    aid = payload.get("approval_id")
    if not aid:
        return
    if no_input or not sys.stdin.isatty():
        await _decide_quietly(svc, run_id, aid, Decision.DENY, "non-interactive: denied")
        return
    print(f"\n  APPROVAL NEEDED ({payload.get('type')}): {payload.get('reason')}", file=sys.stderr)
    print(f"  effect: {payload.get('side_effect')}   expires in {payload.get('expires_in_s')}s", file=sys.stderr)
    if payload.get("command"):
        print(f"  command: {payload['command']}", file=sys.stderr)
    options = "[y]es once" + (", [a]lways this run" if payload.get("grantable") else "") + (
        ", [m]odify" if payload.get("type") == "command" else "") + ", [n]o, [c]ancel run"
    answer = (await asyncio.to_thread(input, f"  {options}: ")).strip().lower()
    modified = None
    if answer in ("m", "modify"):
        modified = (await asyncio.to_thread(input, "  run instead: ")).strip()
    decision = {"y": Decision.APPROVE_ONCE, "yes": Decision.APPROVE_ONCE, "a": Decision.APPROVE_FOR_RUN,
                "always": Decision.APPROVE_FOR_RUN, "m": Decision.MODIFY, "modify": Decision.MODIFY,
                "c": Decision.CANCEL_RUN, "cancel": Decision.CANCEL_RUN}.get(answer, Decision.DENY)
    try:
        await svc.decide(run_id, aid, decision, actor="cli", modified_command=modified)
    except ApprovalError as exc:
        print(f"  not recorded: {exc}", file=sys.stderr)


async def _decide_quietly(svc, run_id: str, approval_id: str, decision, note: str) -> None:
    from patchquest.domain.approvals import ApprovalError

    try:
        await svc.decide(run_id, approval_id, decision, actor="cli", note=note)
    except ApprovalError:  # already decided or expired: nothing left to deny
        pass


async def _run(args: argparse.Namespace) -> int:
    from patchquest.application import get_service
    from patchquest.config import get_config
    from patchquest.security import RepoPathError

    cfg = get_config()
    if args.promote_policy:
        cfg.agent.promote_policy = args.promote_policy
    svc = get_service()
    try:
        run = svc.create_run(repo_path=args.repo, task=args.task, provider=args.provider, model=args.model,
                             runtime_mode=args.runtime, dry_run=args.dry_run, base_url=args.base_url)
    except (RepoPathError, ValueError) as exc:
        print(f"error: {exc}\nhint: pass --repo pointing at a project directory", file=sys.stderr)
        return EXIT_USAGE
    run_id = run["id"]
    if not args.json:
        print(f"run {run_id}  provider={args.provider} runtime={args.runtime}", file=sys.stderr)
    svc.launch(run_id)
    return await _follow(svc, run_id, args.json, args.no_input)


async def _follow(svc, run_id: str, json_mode: bool, no_input: bool, after_id: int = 0) -> int:
    """Stream a launched run to the terminal, then summarise it. Returns the process exit code."""
    try:
        async for event in svc.stream(run_id, after_id=after_id, heartbeat=1.0):
            if event["type"] == "ping":
                continue
            if json_mode:
                _emit_json(event)
            elif (line := _fmt_event(event)):
                print(line, file=sys.stderr)
            if event["type"] == "approval_requested":
                asyncio.get_running_loop().create_task(_approve_interactively(svc, run_id, event, no_input))
    except (KeyboardInterrupt, asyncio.CancelledError):
        svc.cancel(run_id)
        await svc.shutdown()
        return EXIT_INTERRUPTED
    final = svc.get_run(run_id)
    report = svc.report(run_id) or {}
    summary = {"run_id": run_id, "status": final["status"], "outcome": final.get("outcome"),
               "verdict": final.get("verdict"), "has_diff": bool(report.get("diff_patch"))}
    if json_mode:
        _emit_json({"type": "summary", **summary})
    else:
        print(f"\nstatus={summary['status']} outcome={summary['outcome']} verdict={summary['verdict']}", file=sys.stderr)
        print(f"inspect: patchquest inspect {run_id}   diff: patchquest diff {run_id}", file=sys.stderr)
    if final["status"] in ("cancelled", "interrupted"):
        return EXIT_INTERRUPTED
    if final["status"] != "completed":
        return EXIT_FAILED
    return EXIT_OK if final.get("outcome") in GOOD_OUTCOMES else EXIT_REJECTED


async def _resume(args: argparse.Namespace) -> int:
    from patchquest.application import get_service
    from patchquest.runtime.resume import ConfirmationRequired, NotResumable, RecoveryCategory, plan_resume

    svc = get_service()
    try:
        plan = plan_resume(args.run_id)
    except LookupError:
        print(f"error: no run {args.run_id}\nhint: `patchquest status` lists recent runs", file=sys.stderr)
        return EXIT_USAGE
    explanation = plan.explain()
    if args.json:
        _emit_json({"type": "resume_plan", **explanation})
    else:
        print(f"resume {args.run_id}", file=sys.stderr)
        for key, value in explanation.items():
            if key not in ("REASONS", "CATEGORY"):
                print(f"  {key:<22} {value}", file=sys.stderr)
        for reason in plan.reasons:
            print(f"  - {reason}", file=sys.stderr)
    if plan.category is RecoveryCategory.NON_RECOVERABLE:
        return EXIT_FAILED
    if args.plan:
        return EXIT_NEEDS_CONFIRMATION if plan.needs_confirmation else EXIT_OK
    with get_db() as conn:
        seen = conn.execute("SELECT COALESCE(MAX(id), 0) FROM run_events WHERE run_id = ?", (args.run_id,)).fetchone()[0]
    try:
        svc.resume(args.run_id, accept_drift=args.accept_drift, rollback=args.rollback)
    except ConfirmationRequired as exc:
        flag = "--rollback" if exc.plan.category is RecoveryCategory.ROLLBACK_REQUIRED else "--accept-drift"
        print(f"\nnot resumed: a person must decide first. Review the above, then re-run with {flag}.", file=sys.stderr)
        return EXIT_NEEDS_CONFIRMATION
    except NotResumable as exc:
        print(f"\nnot resumed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return await _follow(svc, args.run_id, args.json, args.no_input, after_id=seen)


def _parse_overrides(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise ValueError(f"--set expects key=value, got '{pair}'")
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


async def _fork(args: argparse.Namespace) -> int:
    from patchquest.application import get_service
    from patchquest.application.service import ForkBlocked, ForkError, RunNotFound
    from patchquest.security import RepoPathError

    svc = get_service()
    try:
        child = svc.fork(args.run_id, from_seq=args.from_seq, provider=args.provider, model=args.model,
                         base_url=args.base_url, overrides=_parse_overrides(args.set or []), accept_drift=args.accept_drift)
    except RunNotFound:
        print(f"error: no run {args.run_id}", file=sys.stderr)
        return EXIT_USAGE
    except (ForkError, ValueError, RepoPathError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ForkBlocked as exc:
        print(f"not forked: files changed since that checkpoint ({exc}). Review them, then re-run with --accept-drift.",
              file=sys.stderr)
        return EXIT_NEEDS_CONFIRMATION
    if not args.json:
        print(f"fork {child['id']}  of {args.run_id[:8]}", file=sys.stderr)
    return await _follow(svc, child["id"], args.json, args.no_input)


async def _replay(args: argparse.Namespace) -> int:
    from patchquest.application import get_service
    from patchquest.application.service import RunNotFound
    from patchquest.runtime.replay import NotReplayable, ReplayMode, StateReplay, compare_runs, comparison_payload

    svc = get_service()
    try:
        result = svc.replay(args.run_id, ReplayMode(args.mode))
    except (RunNotFound, LookupError):
        print(f"error: no run {args.run_id}", file=sys.stderr)
        return EXIT_USAGE
    except NotReplayable as exc:
        print(f"error: cannot replay: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if isinstance(result, StateReplay):
        payload = {"run_id": result.run_id, "ok": result.ok, "findings": list(result.findings),
                   "status_trail": list(result.status_trail), "phases": result.phases, "events": result.events,
                   "checkpoints": result.checkpoints}
        if args.json:
            _emit_json(payload)
        else:
            print(f"{'consistent' if result.ok else 'INCONSISTENT'}: {result.events} events, {result.checkpoints} checkpoints, "
                  f"status trail {' -> '.join(result.status_trail) or '(none)'}")
            for finding in result.findings:
                print(f"  ! {finding}")
        return EXIT_OK if result.ok else EXIT_FAILED
    replay_id = result["id"]
    if not args.json:
        print(f"replay {replay_id} ({args.mode}) of {args.run_id[:8]}: nothing is written to the repository", file=sys.stderr)
    code = await _follow(svc, replay_id, args.json, True)
    comparison = comparison_payload(compare_runs(args.run_id, replay_id))
    if args.json:
        _emit_json({"type": "comparison", **comparison})
    elif comparison["matched"]:
        print("matches the original run", file=sys.stderr)
    else:
        for d in comparison["divergences"]:
            print(f"  differs: {d['aspect']}: original={d['original']!r} replay={d['replay']!r}", file=sys.stderr)
    return EXIT_OK if comparison["matched"] and code in (EXIT_OK, EXIT_REJECTED) else EXIT_FAILED


def _cmd_lineage(args: argparse.Namespace) -> int:
    from patchquest.application import get_service

    info = get_service().lineage(args.run_id)
    if args.json:
        _emit_json(info)
        return EXIT_OK
    for r in info["ancestry"]:
        via = f" ({r['lineage_kind']} from checkpoint {r['parent_checkpoint_seq']})" if r["lineage_kind"] else ""
        print(f"{r['id'][:8]}  {r['status']:<11} {(r.get('model') or '-'):<24}{via}")
    for c in info["children"]:
        print(f"  child {c['id'][:8]}  {c['lineage_kind']:<7} {c['status']:<11} {c.get('model') or '-'}")
    return EXIT_OK


def _cmd_checkpoints(args: argparse.Namespace) -> int:
    from patchquest.persistence import checkpoints

    with get_db() as conn:
        rows = checkpoints.describe(conn, args.run_id)
    if args.json:
        _emit_json(rows)
        return EXIT_OK
    if not rows:
        print("(no checkpoints: the run has not completed a phase yet)", file=sys.stderr)
    for r in rows:
        print(f"#{r['seq']:<3} after {r['phase']:<17} attempt {r['attempt']}  {r['bytes']:>8} B  {r['created_at'][:19]}  {r['status']}")
    return EXIT_OK if all(r["status"] == "ok" for r in rows) else EXIT_FAILED


def _cmd_events(args: argparse.Namespace) -> int:
    from patchquest.persistence import ledger

    with get_db() as conn:
        rows = ledger.read(conn, args.run_id, after=args.after, limit=args.limit)
    for e in rows:
        if args.json:
            _emit_json(e)
        else:
            print(f"{e['id']:>5} {e['created_at'][11:19]} a{e['attempt']} {(e.get('actor') or '-'):<9} "
                  f"{e['type']:<22} {(e.get('phase') or ''):<16} {e.get('message') or ''}")
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    from patchquest.application import get_service
    from patchquest.application.service import RunNotFound

    svc = get_service()
    if args.run_id:
        try:
            runs = [svc.get_run(args.run_id)]
        except RunNotFound:
            print(f"error: no run {args.run_id}\nhint: `patchquest status` lists recent runs", file=sys.stderr)
            return EXIT_USAGE
    else:
        runs = svc.list_runs(args.limit)
    if args.json:
        _emit_json([{**r, "budget": svc.budget(r["id"])} for r in runs] if args.run_id else runs)
        return EXIT_OK
    for r in runs:
        print(f"{r['id'][:8]}  {r['status']:<16} {(r.get('outcome') or '-'):<11} {(r.get('verdict') or '-'):<10} {r['task'][:60]}")
    if args.run_id:
        for line in svc.budget(args.run_id):
            limit = "unlimited" if not line["limit"] else f"{line['limit']:g}"
            print(f"  budget {line['kind']:<14} {line['used']:g} / {limit}{'  EXHAUSTED' if line['exhausted'] else ''}")
    return EXIT_OK


def _cmd_inspect(args: argparse.Namespace) -> int:
    from patchquest.application import get_service
    from patchquest.application.service import RunNotFound

    svc = get_service()
    try:
        run = svc.get_run(args.run_id)
        events = svc.events(args.run_id)
    except RunNotFound:
        print(f"error: no run {args.run_id}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        _emit_json({"run": run, "events": events})
        return EXIT_OK
    print(f"run {run['id']}  status={run['status']} outcome={run.get('outcome')} verdict={run.get('verdict')}")
    print(f"task: {run['task']}\nrepo: {run['repo_path']}\n")
    for e in events:
        print(f"{e['created_at'][11:19]}  {e['type']:<22} {(e.get('phase') or ''):<16} {e.get('message') or ''}")
    return EXIT_OK


def _cmd_diff(args: argparse.Namespace) -> int:
    from patchquest.application import get_service

    diff = get_service().diff(args.run_id)
    if not diff:
        print("(no diff recorded for this run)", file=sys.stderr)
        return EXIT_FAILED
    sys.stdout.write(diff)
    return EXIT_OK


def _cmd_report(args: argparse.Namespace) -> int:
    from patchquest.application import get_service

    report = get_service().report(args.run_id)
    if not report:
        print("error: no report for this run", file=sys.stderr)
        return EXIT_FAILED
    print(report["report_md"])
    return EXIT_OK


def _cmd_approve(args: argparse.Namespace) -> int:
    import httpx

    from patchquest.config import get_config

    cfg = get_config()
    token = os.environ.get(cfg.api_token_env, "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    decision = args.decision or ("DENY" if args.deny else "APPROVE_ONCE")
    url = f"{args.server.rstrip('/')}/api/runs/{args.run_id}/approvals/{args.approval_id}"
    r = httpx.post(url, json={"decision": decision, "modified_command": args.command}, headers=headers, timeout=10)
    if r.status_code != 200:
        print(f"error: server said {r.status_code}: {r.text}", file=sys.stderr)
        return EXIT_FAILED
    print("ok")
    return EXIT_OK


def _cmd_providers(args: argparse.Namespace) -> int:
    from patchquest.providers.catalog import PROVIDER_CATALOG

    if args.probe or args.url:
        return _probe_providers(args)

    rows = []
    for p in PROVIDER_CATALOG:
        env = p.get("api_key_env")
        rows.append({"name": p["name"], "default_model": p.get("default_model"), "key_env": env,
                     "configured": True if not env else bool(os.environ.get(env))})
    if args.json:
        _emit_json(rows)
        return EXIT_OK
    for r in rows:
        mark = ICON["ok"] if r["configured"] else ICON["warn"]
        print(f"{mark} {r['name']:<18} {r['default_model'] or '':<32} {r['key_env'] or ''}")
    return EXIT_OK


def _cmd_admin(args: argparse.Namespace) -> int:
    """Organisation, token and audit administration. Direct database access: whoever can run this owns the install."""
    from patchquest.domain.identity import Role
    from patchquest.persistence import identity as ids

    def out(obj: Any, human: str) -> int:
        if args.json:
            _emit_json(obj)
        else:
            print(human)
        return EXIT_OK

    with get_db() as conn:
        if args.admin_cmd == "init":
            org = ids.create_org(conn, args.org)
            ws = ids.create_workspace(conn, org, args.workspace)
            owner = ids.create_principal(conn, org, args.owner)
            ids.set_role(conn, owner, ws, Role.OWNER)
            token_id, secret = ids.issue_token(conn, owner, "initial owner token")
            ids.audit(conn, "org.init", actor="cli", org_id=org, workspace_id=ws, target=owner)
            result = {"org_id": org, "workspace_id": ws, "owner_id": owner, "token_id": token_id, "token": secret}
            return out(result, f"organisation {org}\nworkspace    {ws}\nowner        {owner}\ntoken        {secret}\n"
                               "Store the token now; it is not shown again.")
        if args.admin_cmd == "token":
            if args.op == "create":
                row = conn.execute("SELECT p.id, p.org_id FROM principals p WHERE p.name = ? AND p.org_id = "
                                   "(SELECT org_id FROM workspaces WHERE id = ?)", (args.principal, args.workspace)).fetchone()
                if row is None:
                    pid = ids.create_principal(conn, conn.execute("SELECT org_id FROM workspaces WHERE id = ?",
                                                                  (args.workspace,)).fetchone()["org_id"], args.principal,
                                               "service" if args.role == Role.SERVICE.value else "user")
                else:
                    pid = row["id"]
                ids.set_role(conn, pid, args.workspace, Role(args.role))
                token_id, secret = ids.issue_token(conn, pid, args.label or "", args.expires_days)
                ids.audit(conn, "token.create", actor="cli", workspace_id=args.workspace, target=token_id,
                          detail={"principal": args.principal, "role": args.role})
                return out({"token_id": token_id, "token": secret, "principal_id": pid},
                           f"token {token_id}\n{secret}\nStore it now; it is not shown again.")
            if args.op == "revoke":
                ok = ids.revoke_token(conn, args.token_id)
                ids.audit(conn, "token.revoke", actor="cli", target=args.token_id, outcome="ok" if ok else "not_found")
                return out({"revoked": ok}, "revoked" if ok else "no active token with that id") if ok else EXIT_USAGE
            rows = ids.list_tokens(conn)
            return out(rows, "\n".join(f"{r['id']}  {r['prefix']}…  {r['principal']:<16} {r['label'] or '':<20} "
                                        f"{'REVOKED' if r['revoked_at'] else 'active'}" for r in rows) or "(no tokens)")
        if args.admin_cmd == "audit":
            rows = ids.read_audit(conn, None, after=args.after, limit=args.limit)
            return out(rows, "\n".join(f"{r['id']:>5} {r['ts'][11:19]} {r['actor']:<22} {r['action']:<18} {r['outcome']:<8} "
                                        f"{r['workspace_id'] or '-':<20} {r['target'] or ''}" for r in rows) or "(no entries)")
    return EXIT_USAGE


def _cmd_metrics(args: argparse.Namespace) -> int:
    from patchquest.config import get_config
    from patchquest.observability.metrics import MetricsQuery, compute, parse_window

    try:
        query = MetricsQuery(since=parse_window(args.window), group_by=args.by, pricing=get_config().pricing)
        with get_db() as conn:
            result = compute(conn, query)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        _emit_json(result)
        return EXIT_OK

    def show(title: str, m: dict[str, Any]) -> None:
        def pct(v: float | None) -> str:
            return "-" if v is None else f"{v * 100:.0f}%"

        def secs(d: dict[str, Any]) -> str:
            return "-" if d["p50"] is None else f"p50 {d['p50']:.1f}s  p95 {d['p95']:.1f}s"

        print(f"{title}: {m['runs']} runs ({m['finished']} finished)")
        print(f"  success {pct(m['task_success_rate'])}   validation {pct(m['validation_pass_rate'])}   "
              f"first-pass {pct(m['first_pass_success_rate'])}   resume {pct(m['resume_success_rate'])}")
        print(f"  time to completion {secs(m['time_to_completion_s'])}   approvals {m['human_interventions']} ({secs(m['approval_latency_s'])})")
        print(f"  model calls/run {m['mean_model_calls'] or '-'}   tokens {m['tokens']['total']}   "
              f"compute {m['model_compute_s']}s   retries {pct(m['retry_rate'])}")
        if m["failure_distribution"]:
            print("  failures: " + ", ".join(f"{k} x{v}" for k, v in m["failure_distribution"].items()))

    show(f"last {args.window}", result["totals"])
    for name, block in (result.get("groups") or {}).items():
        show(f"{args.by} {name}", block)
    for row in result["models"]:
        lat = row["latency_ms"]
        print(f"  {row['provider']}/{row['model']}: {row['calls']} calls, error rate {row['error_rate']}, "
              f"p50 {lat['p50']}ms p95 {lat['p95']}ms")
    return EXIT_OK


def _cmd_trace(args: argparse.Namespace) -> int:
    from patchquest.observability.trace import build_trace

    try:
        with get_db() as conn:
            trace = build_trace(conn, args.run_id)
    except LookupError:
        print(f"error: no run {args.run_id}", file=sys.stderr)
        return EXIT_USAGE
    if args.endpoint:
        import httpx

        try:
            resp = httpx.post(args.endpoint, json=trace, timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"error: could not export to {args.endpoint}: {exc}", file=sys.stderr)
            return EXIT_FAILED
        print(f"exported {len(trace['resourceSpans'][0]['scopeSpans'][0]['spans'])} spans to {args.endpoint}", file=sys.stderr)
        return EXIT_OK
    _emit_json(trace)
    return EXIT_OK


def _load_definition(path: str) -> dict[str, Any]:
    import yaml

    with open(path, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)  # JSON is valid YAML, so one loader reads both
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} does not contain a workflow definition")
    return loaded


async def _workflows(args: argparse.Namespace) -> int:
    from patchquest.domain.identity import LOCAL_WORKSPACE_ID
    from patchquest.domain.workflows import DefinitionError, parse
    from patchquest.workflows import store
    from patchquest.workflows.engine import WorkflowError
    from patchquest.workflows.runtime import get_engine
    from patchquest.workflows.templates import TEMPLATES, instantiate

    engine = get_engine()
    cmd = args.wf_cmd

    def problems_out(items: list[Any]) -> int:
        for p in items:
            print(f"  ✗ {p.message}", file=sys.stderr)
        return EXIT_USAGE

    if cmd == "templates":
        rows = [{"name": n, "description": t["description"], "trigger": t["trigger"]["type"]} for n, t in sorted(TEMPLATES.items())]
        if args.json:
            _emit_json(rows)
        else:
            for r in rows:
                print(f"{r['name']:<22} {r['trigger']:<32} {r['description']}")
        return EXIT_OK
    if cmd in ("validate", "save"):
        try:
            raw = instantiate(args.template, **dict(v.split("=", 1) for v in args.var or [])) if args.template else _load_definition(args.file)
            wf = parse(raw)
        except DefinitionError as exc:
            return problems_out(exc.problems)
        except (OSError, ValueError, KeyError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        found = engine.check(wf)
        if found:
            return problems_out(found)
        if cmd == "validate":
            print("valid", file=sys.stderr)
            return EXIT_OK
        with get_db() as conn:
            wf_id, version = store.save_version(conn, args.workspace or LOCAL_WORKSPACE_ID, wf, "cli")
        _emit_json({"id": wf_id, "name": wf.name, "version": version}) if args.json else print(f"saved {wf.name} v{version} ({wf_id})")
        return EXIT_OK
    if cmd == "list":
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, workspace_id, name, version, trigger_type FROM workflows w WHERE version = (SELECT MAX(version) FROM workflows x "
                "WHERE x.workspace_id = w.workspace_id AND x.name = w.name) ORDER BY name")]
        _emit_json(rows) if args.json else [print(f"{r['id']}  {r['name']:<24} v{r['version']:<3} {r['trigger_type']}") for r in rows]
        return EXIT_OK
    if cmd == "runs":
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, workflow_id, status, created_at, error FROM workflow_runs ORDER BY created_at DESC LIMIT ?", (args.limit,))]
        _emit_json(rows) if args.json else [print(f"{r['id']}  {r['status']:<10} {r['created_at'][:19]}  {r['error'] or ''}") for r in rows]
        return EXIT_OK
    try:
        if cmd == "start":
            variables = dict(v.split("=", 1) for v in args.var or [])
            run_id = engine.start(args.workflow_id, {"type": "manual", "payload": {}}, variables=variables, created_by="cli")
            print(f"workflow run {run_id}", file=sys.stderr)
            return await _follow_workflow(engine, str(run_id), args.json)
        if cmd == "show":
            with get_db() as conn:
                run = store.get_run(conn, args.run_id)
                payload = {**run, "steps": store.steps(conn, args.run_id), "events": store.events(conn, args.run_id)}
            if args.json:
                _emit_json(payload)
            else:
                print(f"{run['id']}  {run['status']}  {run['error'] or ''}")
                for s in payload["steps"]:
                    print(f"  {s['node_id']:<16} visit {s['visit']}  {s['status']}")
            return EXIT_OK
        if cmd == "decide":
            await engine.decide(args.run_id, args.node_id, args.decision, "cli")
            return await _follow_workflow(engine, args.run_id, args.json)
        if cmd == "cancel":
            await engine.cancel(args.run_id, "cli")
            return EXIT_OK
    except (WorkflowError, store.WorkflowNotFound) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_USAGE


async def _follow_workflow(engine: Any, run_id: str, json_mode: bool) -> int:
    """Advance the run, printing new events, until it ends or needs a person."""
    from patchquest.workflows import store

    seen = 0
    while True:
        await engine.tick()
        with get_db() as conn:
            run = store.get_run(conn, run_id)
            new = store.events(conn, run_id, seen)
            waiting = [s for s in store.steps(conn, run_id) if s["status"] in ("waiting", "uncertain") and s["wait_kind"] != "child_run"]
        for e in new:
            seen = e["id"]
            _emit_json(e) if json_mode else print(f"  {e['type']:<20} {e['node_id'] or '':<14} {e['message'] or ''}", file=sys.stderr)
        if run["status"] in ("completed", "failed", "cancelled"):
            print(f"workflow {run['status']}" + (f": {run['error']}" if run["error"] else ""), file=sys.stderr)
            return EXIT_OK if run["status"] == "completed" else EXIT_FAILED
        if waiting and not any(s["wait_kind"] == "child_run" for s in waiting):
            for s in waiting:
                hint = f"patchquest workflows decide {run_id} {s['node_id']} approve|deny" if s["wait_kind"] == "approval" else s["wait_kind"] or s["status"]
                print(f"waiting at {s['node_id']}: {hint}", file=sys.stderr)
            return EXIT_NEEDS_CONFIRMATION
        await asyncio.sleep(0.4)


async def _worker(args: argparse.Namespace) -> int:
    import signal

    from patchquest.application import get_service
    from patchquest.config import get_config
    from patchquest.runtime.worker import Worker

    worker = Worker(get_service(), worker_id=args.id, lease_s=args.lease or get_config().worker_lease_seconds, poll_s=args.poll)
    if args.once:
        await worker.run_once()
        return EXIT_OK
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    print(f"worker {worker.id}: lease {worker.lease_s:g}s, polling every {worker.poll_s:g}s (Ctrl-C to stop)", file=sys.stderr)
    runner = asyncio.create_task(worker.run_forever(stop))
    await stop.wait()
    try:  # finish the run in hand if it ends soon, else leave it for another worker to recover
        handled = await asyncio.wait_for(asyncio.shield(runner), timeout=args.grace)
    except TimeoutError:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        print("stopped with a run in progress; another worker will recover it when its lease expires", file=sys.stderr)
        return EXIT_INTERRUPTED
    print(f"worker {worker.id} stopped after {handled} run(s)", file=sys.stderr)
    return EXIT_OK


def _cmd_queue(args: argparse.Namespace) -> int:
    from patchquest.runtime import queue

    stats = queue.stats()
    if args.json:
        _emit_json(stats)
    else:
        wait = "-" if stats["oldest_wait_s"] is None else f"{stats['oldest_wait_s']:.0f}s"
        print(f"queued {stats['queued']}   running with a live lease {stats['leased']}   expired leases {stats['expired_leases']}   "
              f"oldest waiting {wait}")
    return EXIT_OK


def _cmd_backup(args: argparse.Namespace) -> int:
    from pathlib import Path

    from patchquest import backup
    from patchquest.database import get_db_path

    def show(report: backup.VerifyReport) -> None:
        if args.json:
            _emit_json(report.to_dict())
            return
        print(f"{'ok' if report.ok else 'PROBLEMS'}: schema v{report.schema_version}, " + ", ".join(f"{k} {v}" for k, v in report.counts.items()))
        for problem in report.problems:
            print(f"  - {problem}", file=sys.stderr)

    try:
        if args.backup_cmd == "create":
            report = backup.create(get_db_path(), Path(args.dest))
            show(report)
            return EXIT_OK if report.ok else EXIT_FAILED
        if args.backup_cmd == "verify":
            report = backup.verify(Path(args.file))
            show(report)
            return EXIT_OK if report.ok else EXIT_FAILED
        if not args.yes:
            print("restore replaces the live database with the backup (the current one is kept beside it). Re-run with --yes.", file=sys.stderr)
            return EXIT_NEEDS_CONFIRMATION
        kept = backup.restore(Path(args.file), get_db_path())
        print(f"restored; the previous database was kept at {kept}", file=sys.stderr)
        return EXIT_OK
    except (backup.RestoreRefused, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED


def _cmd_engines(args: argparse.Namespace) -> int:
    from patchquest.providers.engines import engine_report

    rows = asyncio.run(engine_report())
    if args.json:
        _emit_json(rows)
        return EXIT_OK
    for r in rows:
        if r["available"]:
            ctx = f" ctx={r['context_limit']}" if r["context_limit"] else ""
            loaded = ", ".join(r["models"][:2]) if r["model_loaded"] else "no model loaded"
            print(f"{ICON['ok']} {r['engine']:<9} up {r['latency_ms']}ms  {loaded}{ctx}")
        else:
            print(f"{ICON['info']} {r['engine']:<9} not running  ({r['last_error']})")
    return EXIT_OK


def _probe_providers(args: argparse.Namespace) -> int:
    from patchquest.providers.probe import probe_endpoint, probe_local_engines

    if args.url:
        results = {args.url: asyncio.run(probe_endpoint(args.url, args.api_key_env))}
    else:
        results = asyncio.run(probe_local_engines())
    if args.json:
        _emit_json(results)
        return EXIT_OK
    for name, r in results.items():
        if r["ok"]:
            ctx = f" ctx={r['context_length']}" if r.get("context_length") else ""
            print(f"{ICON['ok']} {name:<10} up  {r['latency_ms']}ms  models={', '.join(r['models'][:3]) or '-'}{ctx}")
        else:
            print(f"{ICON['info']} {name:<10} down  {r.get('error')}")
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    from patchquest.doctor import FAIL, run_checks

    if args.bundle:
        from pathlib import Path

        from patchquest import support_bundle

        try:
            names = support_bundle.build(Path(args.bundle), run_id=args.run)
        except (FileExistsError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FAILED
        print(f"wrote {args.bundle} ({', '.join(names)}). It holds no source, prompts, diffs, task text or keys; read it before sharing.",
              file=sys.stderr)
        return EXIT_OK
    checks = run_checks()
    if args.json:
        _emit_json([c.to_dict() for c in checks])
    else:
        for c in checks:
            print(f"{ICON[c.status]} {c.name:<16} {c.detail}")
            if c.impact and c.status in ("warn", "fail"):
                print(f"    impact: {c.impact}")
            if c.fix and c.status != "ok":
                print(f"    fix: {c.fix}")
    return EXIT_FAILED if any(c.status == FAIL for c in checks) else EXIT_OK


def _cmd_eval(args: argparse.Namespace) -> int:
    from pathlib import Path

    from patchquest.evaluation import compare, load_corpus, run_eval

    if args.eval_cmd == "list":
        tasks = load_corpus(args.corpus, args.filter)
        if args.json:
            _emit_json([{"id": t.id, "category": t.category, "difficulty": t.difficulty, "task": t.task} for t in tasks])
        else:
            for t in tasks:
                print(f"{t.id:34s} {t.category:12s} {t.difficulty:7s} {t.task[:70]}")
        return EXIT_OK

    if args.eval_cmd == "compare":
        base, new = json.loads(Path(args.base).read_text()), json.loads(Path(args.new).read_text())
        try:
            diff = compare(base, new)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if args.json:
            _emit_json(diff)
        else:
            sr = diff["success_rate"]
            print(f"success rate {sr['base']:.1%} -> {sr['new']:.1%} ({sr['delta']:+.1%})")
            print(f"regressions: {', '.join(diff['regressions']) or 'none'}")
            print(f"fixes:       {', '.join(diff['fixes']) or 'none'}")
        return EXIT_FAILED if diff["regressions"] else EXIT_OK

    if args.eval_cmd in ("recovery", "matrix", "experiment", "gate"):
        return _eval_extended(args)

    def progress(r) -> None:
        if not args.json:
            print(f"{ICON['ok'] if r.status == 'success' else ICON['warn'] if r.status == 'partial' else ICON['fail']} "
                  f"{r.id:34s} {r.status:8s} {r.failure_reason or '':18s} calls={r.model_calls} {r.wall_s}s", file=sys.stderr)

    try:
        report = asyncio.run(run_eval(corpus=args.corpus, only=args.filter, provider=args.provider, model=args.model,
                                      base_url=args.base_url, time_limit=args.timeout, progress=progress))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    data = report.to_dict()
    if args.out:
        Path(args.out).write_text(json.dumps(data, indent=2, default=str))
    overall = data["summary"]["overall"]
    if args.json:
        _emit_json(data)
    else:
        print(f"\nsuccess {overall['success']}/{overall['tasks']} ({overall['success_rate']:.1%})  "
              f"partial {overall['partial']}  failure {overall['failure']}  tokens {data['summary']['totals']['tokens']}")
        for cat, b in data["summary"]["by_category"].items():
            print(f"  {cat:12s} {b['success']}/{b['tasks']}")
        if data["summary"]["failure_reasons"]:
            print("  failure reasons:", data["summary"]["failure_reasons"])
        if args.out:
            print(f"results written to {args.out}")
    return EXIT_FAILED if overall["success_rate"] < args.fail_under else EXIT_OK


def _eval_extended(args: argparse.Namespace) -> int:
    from pathlib import Path

    from patchquest.evaluation import experiments, gates, recovery

    def emit(data: dict[str, Any]) -> None:
        if args.out:
            Path(args.out).write_text(json.dumps(data, indent=2, default=str))
        if args.json:
            _emit_json(data)

    try:
        if args.eval_cmd == "recovery":
            data = asyncio.run(recovery.run_recovery(only=args.filter))
            emit(data)
            if not args.json:
                for sc in data["scenarios"]:
                    print(f"{ICON['ok'] if sc['passed'] else ICON['fail']} {sc['id']:32s} {sc['category'] or '-':28s} "
                          f"{sc['status']}/{sc['outcome']} calls={sc['model_calls']}")
                    for failure in sc["failures"]:
                        print(f"    - {failure}", file=sys.stderr)
                s = data["summary"]
                print(f"\n{s['passed']}/{s['scenarios']} scenarios ended correct and safe")
            return EXIT_OK if data["summary"]["passed"] == data["summary"]["scenarios"] else EXIT_FAILED
        if args.eval_cmd == "matrix":
            targets = [experiments.parse_target(t) for t in args.target]
            data = asyncio.run(experiments.run_matrix(targets, corpus=args.corpus, only=args.filter, time_limit=args.timeout))
            emit(data)
            if not args.json:
                print(f"{'target':36s} {'success':>9s} {'tokens':>9s} {'calls':>6s} {'wall s':>8s}  attribution")
                for row in data["table"]:
                    print(f"{row['target']:36s} {row['success']:>4d}/{row['tasks']:<4d} {row['tokens']:>9d} {row['model_calls']:>6d} "
                          f"{row['wall_s']:>8.1f}  {row['attribution']}")
            return EXIT_OK
        if args.eval_cmd == "experiment":
            data = asyncio.run(experiments.run_experiment(
                baseline=_parse_overrides(args.baseline or []), candidate=_parse_overrides(args.candidate or []),
                target=experiments.Target(args.provider, args.model, args.base_url), corpus=args.corpus, only=args.filter,
                time_limit=args.timeout))
            emit(data)
            if not args.json:
                p = data["paired"]
                print(f"baseline  {data['baseline']['success']}/{data['tasks']}   candidate  {data['candidate']['success']}/{data['tasks']}")
                print(f"candidate wins {p['candidate_wins']}  losses {p['candidate_losses']}  ties {p['ties']}  sign-test p={p['sign_test_p']}")
                print(data["reading"])
            return EXIT_OK
        data = asyncio.run(gates.run_gate(
            args.tier, provider=args.provider, model=args.model, base_url=args.base_url, baseline=args.baseline,
            fail_under=args.fail_under, time_limit=args.timeout,
            progress=None if args.json else lambda r: print(
                f"{ICON['info'] if r.skipped else ICON['ok'] if r.passed else ICON['fail']} tier {r.tier} ({r.name}): "
                f"{'skipped' if r.skipped else 'passed' if r.passed else 'FAILED'}", file=sys.stderr)))
        emit(data)
        for tier in data["tiers"]:
            for problem in tier["problems"]:
                print(f"    - {problem}", file=sys.stderr)
        return EXIT_OK if data["passed"] else EXIT_FAILED
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from patchquest.config import get_config
    from patchquest.security import check_startup_policy

    cfg = get_config()
    host, port = args.host or cfg.host, args.port or cfg.port
    try:
        check_startup_policy(host)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    uvicorn.run("patchquest.main:app", host=host, port=port)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    from patchquest.domain.identity import Role

    p = argparse.ArgumentParser(prog="patchquest", description="Local-first agentic coding harness")
    p.add_argument("--config", help="path to config.yaml (default: $PATCHQUEST_CONFIG or ./config.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a task against a repository")
    r.add_argument("--repo", default=".", help="repository directory (default: .)")
    r.add_argument("--task", required=True, help="what to do")
    r.add_argument("--provider", default="mock")
    r.add_argument("--model")
    r.add_argument("--base-url", help="OpenAI-compatible endpoint for this run, e.g. http://localhost:30000/v1")
    r.add_argument("--runtime", choices=["local", "docker"], default="local")
    r.add_argument("--dry-run", action="store_true", help="analyse only; never modify files")
    r.add_argument("--promote-policy", choices=["on_green", "on_no_regression", "always", "never"])
    r.add_argument("--no-input", action="store_true", help="deny every approval request instead of prompting")
    r.add_argument("--json", action="store_true", help="emit JSON lines on stdout")

    s = sub.add_parser("status", help="list runs, or show one")
    s.add_argument("run_id", nargs="?")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    for name, helptext in (("inspect", "full event timeline of a run"), ("diff", "the diff a run produced"),
                           ("report", "the final report of a run")):
        x = sub.add_parser(name, help=helptext)
        x.add_argument("run_id")
        if name == "inspect":
            x.add_argument("--json", action="store_true")
    rs = sub.add_parser("resume", help="continue an interrupted run from its last checkpoint")
    rs.add_argument("run_id")
    rs.add_argument("--plan", action="store_true", help="only explain what resuming would do")
    rs.add_argument("--accept-drift", action="store_true", help="proceed although files changed while the run was down")
    rs.add_argument("--rollback", action="store_true", help="first undo a half-applied promotion")
    rs.add_argument("--no-input", action="store_true", help="deny every approval request instead of prompting")
    rs.add_argument("--json", action="store_true")
    fk = sub.add_parser("fork", help="start a new run from a checkpoint of another, optionally with a different model")
    fk.add_argument("run_id")
    fk.add_argument("--from", dest="from_seq", type=int, help="checkpoint number (default: the latest valid one)")
    fk.add_argument("--provider")
    fk.add_argument("--model")
    fk.add_argument("--base-url")
    fk.add_argument("--set", action="append", metavar="agent.KEY=VALUE", help="override a setting for the fork only")
    fk.add_argument("--accept-drift", action="store_true", help="fork although files changed since the checkpoint")
    fk.add_argument("--no-input", action="store_true")
    fk.add_argument("--json", action="store_true")
    rp = sub.add_parser("replay", help="re-run a past run without side effects, or verify its history")
    rp.add_argument("run_id")
    rp.add_argument("--mode", choices=["state", "model", "live"], default="state",
                    help="state: verify the event history; model: reuse recorded model answers; live: ask the model again")
    rp.add_argument("--json", action="store_true")
    ln = sub.add_parser("lineage", help="where a run came from and what was forked or replayed from it")
    ln.add_argument("run_id")
    ln.add_argument("--json", action="store_true")
    cps = sub.add_parser("checkpoints", help="list a run's checkpoints and whether each verifies")
    cps.add_argument("run_id")
    cps.add_argument("--json", action="store_true")
    evs = sub.add_parser("events", help="the raw event ledger of a run")
    evs.add_argument("run_id")
    evs.add_argument("--after", type=int, default=0, help="only events after this id")
    evs.add_argument("--limit", type=int, default=1000)
    evs.add_argument("--json", action="store_true")
    a = sub.add_parser("approve", help="answer an approval request on a running server")
    a.add_argument("run_id")
    a.add_argument("approval_id")
    a.add_argument("--deny", action="store_true")
    a.add_argument("--decision", choices=["APPROVE_ONCE", "APPROVE_FOR_RUN", "DENY", "MODIFY", "CANCEL_RUN"])
    a.add_argument("--command", help="with --decision MODIFY: the command to run instead")
    a.add_argument("--server", default="http://127.0.0.1:8000")
    pr = sub.add_parser("providers", help="list model providers and whether they are configured")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--probe", action="store_true", help="check which local serving engines are running")
    pr.add_argument("--url", help="probe one OpenAI-compatible base URL (e.g. http://localhost:30000/v1)")
    pr.add_argument("--api-key-env", help="env var holding the key for --url")
    ad = sub.add_parser("admin", help="organisations, API tokens and the audit log")
    asub = ad.add_subparsers(dest="admin_cmd", required=True)
    as_json = argparse.ArgumentParser(add_help=False)
    as_json.add_argument("--json", action="store_true")
    ai = asub.add_parser("init", help="create an organisation, a workspace and an owner with a first token", parents=[as_json])
    ai.add_argument("--org", required=True)
    ai.add_argument("--workspace", required=True)
    ai.add_argument("--owner", required=True)
    at = asub.add_parser("token", help="manage API tokens")
    tsub = at.add_subparsers(dest="op", required=True)
    tc = tsub.add_parser("create", help="issue a token (creates the principal if needed)", parents=[as_json])
    tc.add_argument("--workspace", required=True, help="workspace id")
    tc.add_argument("--principal", required=True, help="person or service name")
    tc.add_argument("--role", choices=[r.value for r in Role], default="DEVELOPER")
    tc.add_argument("--label")
    tc.add_argument("--expires-days", type=float)
    tr = tsub.add_parser("revoke", help="revoke a token", parents=[as_json])
    tr.add_argument("token_id")
    tsub.add_parser("list", help="list tokens (never their secrets)", parents=[as_json])
    aa = asub.add_parser("audit", help="the security audit log", parents=[as_json])
    aa.add_argument("--after", type=int, default=0)
    aa.add_argument("--limit", type=int, default=200)
    mt = sub.add_parser("metrics", help="reliability, latency, token and cost metrics from the run history")
    mt.add_argument("--window", default="7d", help="30m, 24h, 7d, 2w (default 7d)")
    mt.add_argument("--by", choices=["model", "provider", "repository", "workspace"])
    mt.add_argument("--json", action="store_true")
    tr_ = sub.add_parser("trace", help="export a run as an OpenTelemetry trace (OTLP/JSON)")
    tr_.add_argument("run_id")
    tr_.add_argument("--endpoint", help="POST it to an OTLP/HTTP collector, e.g. http://localhost:4318/v1/traces")
    wf = sub.add_parser("workflows", help="durable workflows: define, validate, start, approve")
    wsub = wf.add_subparsers(dest="wf_cmd", required=True)
    for name, helptext in (("templates", "list the ready-made workflows"), ("list", "saved workflows"),
                           ("runs", "recent workflow runs")):
        x = wsub.add_parser(name, help=helptext)
        x.add_argument("--json", action="store_true")
        if name == "runs":
            x.add_argument("--limit", type=int, default=20)
    for name, helptext in (("validate", "check a definition without saving it"), ("save", "save a definition as a new version")):
        x = wsub.add_parser(name, help=helptext)
        x.add_argument("file", nargs="?", help="YAML or JSON definition")
        x.add_argument("--template", help="start from a template (needs --var for its variables)")
        x.add_argument("--var", action="append", metavar="NAME=VALUE", help="bind a template variable")
        x.add_argument("--workspace")
        x.add_argument("--json", action="store_true")
    ws_ = wsub.add_parser("start", help="start a run and follow it until it finishes or needs a person")
    ws_.add_argument("workflow_id")
    ws_.add_argument("--var", action="append", metavar="NAME=VALUE")
    ws_.add_argument("--json", action="store_true")
    sh = wsub.add_parser("show", help="steps and events of a run")
    sh.add_argument("run_id")
    sh.add_argument("--json", action="store_true")
    dc = wsub.add_parser("decide", help="answer an approval step")
    dc.add_argument("run_id")
    dc.add_argument("node_id")
    dc.add_argument("decision", choices=["approve", "deny"])
    dc.add_argument("--json", action="store_true")
    cn = wsub.add_parser("cancel", help="cancel a run")
    cn.add_argument("run_id")
    bk = sub.add_parser("backup", help="consistent database backups, verification and restore")
    bsub = bk.add_subparsers(dest="backup_cmd", required=True)
    bc = bsub.add_parser("create", help="snapshot the database (safe while PatchQuest is running)")
    bc.add_argument("dest")
    bv = bsub.add_parser("verify", help="check a backup (or the live database): integrity, schema, guards, checkpoint checksums")
    bv.add_argument("file")
    br = bsub.add_parser("restore", help="replace the database with a verified backup (stop PatchQuest first)")
    br.add_argument("file")
    br.add_argument("--yes", action="store_true")
    for x in (bc, bv, br):
        x.add_argument("--json", action="store_true")
    wk = sub.add_parser("worker", help="execute queued runs (with queue_mode on); recovers runs whose worker died")
    wk.add_argument("--id", help="worker name (default: host-pid-random)")
    wk.add_argument("--lease", type=float, help="seconds a claimed run stays ours without a heartbeat (default: config)")
    wk.add_argument("--poll", type=float, default=1.0, help="seconds between looking for work")
    wk.add_argument("--grace", type=float, default=30.0, help="on shutdown, seconds to let the current run finish")
    wk.add_argument("--once", action="store_true", help="handle at most one run and exit")
    qs = sub.add_parser("queue", help="how many runs are waiting, leased or have lost their worker")
    qs.add_argument("--json", action="store_true")
    en = sub.add_parser("engines", help="local serving engines: running, model loaded, context limit, capabilities")
    en.add_argument("--json", action="store_true")
    d = sub.add_parser("doctor", help="check the installation, configuration and safety boundaries")
    d.add_argument("--json", action="store_true")
    d.add_argument("--bundle", metavar="ZIP", help="write a sanitized diagnostics bundle (no source, prompts or keys) instead")
    d.add_argument("--run", help="with --bundle: include this run's event history")
    ev = sub.add_parser("eval", help="measure PatchQuest against the evaluation corpus")
    esub = ev.add_subparsers(dest="eval_cmd", required=True)
    el = esub.add_parser("list", help="list corpus tasks")
    er = esub.add_parser("run", help="run the corpus")
    ec = esub.add_parser("compare", help="compare two result files; exit 1 on regressions")
    for x in (el, er):
        x.add_argument("--corpus", help="directory of task YAML files (default: the bundled corpus)")
        x.add_argument("--filter", help="task ids and/or categories, comma-separated")
        x.add_argument("--json", action="store_true")
    er.add_argument("--provider", default="scripted", help="'scripted' replays the reference solutions (default)")
    er.add_argument("--model")
    er.add_argument("--base-url")
    er.add_argument("--timeout", type=float, default=300, help="seconds per task")
    er.add_argument("--out", help="write the full JSON results here")
    er.add_argument("--fail-under", type=float, default=0.0, help="exit 1 if the success rate is lower")
    er2 = esub.add_parser("recovery", help="crash/resume/drift/corruption/cancel scenarios (no model needed)")
    er2.add_argument("--filter")
    er2.add_argument("--out")
    er2.add_argument("--json", action="store_true")
    em = esub.add_parser("matrix", help="the corpus against several provider:model targets")
    em.add_argument("--target", action="append", required=True, metavar="PROVIDER[:MODEL][@BASE_URL]")
    em.add_argument("--corpus")
    em.add_argument("--filter")
    em.add_argument("--timeout", type=float, default=300)
    em.add_argument("--out")
    em.add_argument("--json", action="store_true")
    ex = esub.add_parser("experiment", help="same model and tasks, baseline versus candidate agent.* settings, paired")
    ex.add_argument("--provider", default="scripted")
    ex.add_argument("--model")
    ex.add_argument("--base-url")
    ex.add_argument("--baseline", action="append", metavar="agent.KEY=VALUE")
    ex.add_argument("--candidate", action="append", metavar="agent.KEY=VALUE")
    ex.add_argument("--corpus")
    ex.add_argument("--filter")
    ex.add_argument("--timeout", type=float, default=300)
    ex.add_argument("--out")
    ex.add_argument("--json", action="store_true")
    eg = esub.add_parser("gate", help="regression gates: 0 harness, 1 recovery, 2 replay, 3 live smoke, 4 live corpus")
    eg.add_argument("--tier", type=int, choices=range(5), required=True)
    eg.add_argument("--provider")
    eg.add_argument("--model")
    eg.add_argument("--base-url")
    eg.add_argument("--baseline", help="result file from an earlier live run (tier 4)")
    eg.add_argument("--fail-under", type=float, default=0.0)
    eg.add_argument("--timeout", type=float, default=300)
    eg.add_argument("--out")
    eg.add_argument("--json", action="store_true")
    ec.add_argument("base")
    ec.add_argument("new")
    ec.add_argument("--json", action="store_true")
    sv = sub.add_parser("serve", help="start the API server")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from patchquest.persistence.migrations import SchemaTooNew

    try:
        _bootstrap(args.config)
    except SchemaTooNew as exc:
        if args.cmd != "doctor":  # doctor exists to diagnose exactly this
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FAILED
    if args.cmd == "run":
        return asyncio.run(_run(args))
    if args.cmd == "resume":
        return asyncio.run(_resume(args))
    if args.cmd == "workflows":
        return asyncio.run(_workflows(args))
    if args.cmd == "worker":
        return asyncio.run(_worker(args))
    if args.cmd == "fork":
        return asyncio.run(_fork(args))
    if args.cmd == "replay":
        return asyncio.run(_replay(args))
    handlers = {"backup": _cmd_backup, "queue": _cmd_queue, "trace": _cmd_trace, "metrics": _cmd_metrics, "admin": _cmd_admin, "engines": _cmd_engines, "lineage": _cmd_lineage, "checkpoints": _cmd_checkpoints, "events": _cmd_events, "status": _cmd_status, "inspect": _cmd_inspect, "diff": _cmd_diff, "report": _cmd_report,
                "approve": _cmd_approve, "providers": _cmd_providers, "doctor": _cmd_doctor, "serve": _cmd_serve,
                "eval": _cmd_eval}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

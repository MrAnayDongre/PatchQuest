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
    payload = event.get("payload") or {}
    aid = payload.get("approval_id")
    if not aid:
        return
    if no_input or not sys.stdin.isatty():
        await svc.approve(run_id, aid, False, "non-interactive: denied")
        return
    print(f"\n  APPROVAL NEEDED ({payload.get('type')}): {payload.get('reason')}", file=sys.stderr)
    if payload.get("command"):
        print(f"  command: {payload['command']}", file=sys.stderr)
    answer = await asyncio.to_thread(input, "  approve? [y/N] ")
    await svc.approve(run_id, aid, answer.strip().lower() in ("y", "yes"), "cli")


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
    url = f"{args.server.rstrip('/')}/api/runs/{args.run_id}/approve"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.post(url, json={"approval_id": args.approval_id, "approved": not args.deny}, headers=headers, timeout=10)
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

    checks = run_checks()
    if args.json:
        _emit_json([c.to_dict() for c in checks])
    else:
        for c in checks:
            print(f"{ICON[c.status]} {c.name:<16} {c.detail}")
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
    a.add_argument("--server", default="http://127.0.0.1:8000")
    pr = sub.add_parser("providers", help="list model providers and whether they are configured")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--probe", action="store_true", help="check which local serving engines are running")
    pr.add_argument("--url", help="probe one OpenAI-compatible base URL (e.g. http://localhost:30000/v1)")
    pr.add_argument("--api-key-env", help="env var holding the key for --url")
    d = sub.add_parser("doctor", help="check the installation, configuration and safety boundaries")
    d.add_argument("--json", action="store_true")
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
    ec.add_argument("base")
    ec.add_argument("new")
    ec.add_argument("--json", action="store_true")
    sv = sub.add_parser("serve", help="start the API server")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _bootstrap(args.config)
    if args.cmd == "run":
        return asyncio.run(_run(args))
    if args.cmd == "resume":
        return asyncio.run(_resume(args))
    handlers = {"checkpoints": _cmd_checkpoints, "events": _cmd_events, "status": _cmd_status, "inspect": _cmd_inspect, "diff": _cmd_diff, "report": _cmd_report,
                "approve": _cmd_approve, "providers": _cmd_providers, "doctor": _cmd_doctor, "serve": _cmd_serve,
                "eval": _cmd_eval}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

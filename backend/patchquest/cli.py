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

EXIT_OK, EXIT_FAILED, EXIT_REJECTED, EXIT_INTERRUPTED, EXIT_USAGE = 0, 1, 2, 3, 64
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
                             runtime_mode=args.runtime, dry_run=args.dry_run)
    except RepoPathError as exc:
        print(f"error: {exc}\nhint: pass --repo pointing at a project directory", file=sys.stderr)
        return EXIT_USAGE
    run_id = run["id"]
    if not args.json:
        print(f"run {run_id}  provider={args.provider} runtime={args.runtime}", file=sys.stderr)
    svc.launch(run_id)
    try:
        async for event in svc.stream(run_id, heartbeat=1.0):
            if event["type"] == "ping":
                continue
            if args.json:
                _emit_json(event)
            elif (line := _fmt_event(event)):
                print(line, file=sys.stderr)
            if event["type"] == "approval_requested":
                asyncio.get_running_loop().create_task(_approve_interactively(svc, run_id, event, args.no_input))
    except (KeyboardInterrupt, asyncio.CancelledError):
        svc.cancel(run_id)
        await svc.shutdown()
        return EXIT_INTERRUPTED
    final = svc.get_run(run_id)
    report = svc.report(run_id) or {}
    summary = {"run_id": run_id, "status": final["status"], "outcome": final.get("outcome"),
               "verdict": final.get("verdict"), "has_diff": bool(report.get("diff_patch"))}
    if args.json:
        _emit_json({"type": "summary", **summary})
    else:
        print(f"\nstatus={summary['status']} outcome={summary['outcome']} verdict={summary['verdict']}", file=sys.stderr)
        print(f"inspect: patchquest inspect {run_id}   diff: patchquest diff {run_id}", file=sys.stderr)
    if final["status"] in ("cancelled", "interrupted"):
        return EXIT_INTERRUPTED
    if final["status"] != "completed":
        return EXIT_FAILED
    return EXIT_OK if final.get("outcome") in GOOD_OUTCOMES else EXIT_REJECTED


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
        _emit_json(runs)
        return EXIT_OK
    for r in runs:
        print(f"{r['id'][:8]}  {r['status']:<11} {(r.get('outcome') or '-'):<11} {(r.get('verdict') or '-'):<10} {r['task'][:60]}")
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
    from patchquest.api.routes_providers import PROVIDER_CATALOG

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
    a = sub.add_parser("approve", help="answer an approval request on a running server")
    a.add_argument("run_id")
    a.add_argument("approval_id")
    a.add_argument("--deny", action="store_true")
    a.add_argument("--server", default="http://127.0.0.1:8000")
    pr = sub.add_parser("providers", help="list model providers and whether they are configured")
    pr.add_argument("--json", action="store_true")
    d = sub.add_parser("doctor", help="check the installation, configuration and safety boundaries")
    d.add_argument("--json", action="store_true")
    sv = sub.add_parser("serve", help="start the API server")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _bootstrap(args.config)
    if args.cmd == "run":
        return asyncio.run(_run(args))
    handlers = {"status": _cmd_status, "inspect": _cmd_inspect, "diff": _cmd_diff, "report": _cmd_report,
                "approve": _cmd_approve, "providers": _cmd_providers, "doctor": _cmd_doctor, "serve": _cmd_serve}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

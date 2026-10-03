"""``patchquest demo``: a self-contained demonstration environment (simulated GitHub/Slack, scripted models, real pipeline)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def register(sub: Any) -> None:
    d = sub.add_parser("demo", help="start a seeded, isolated demo (never touches your real data); `demo reset` rebuilds it")
    d.add_argument("action", nargs="?", choices=["start", "reset", "trigger", "transcript", "path"], default="start")
    d.add_argument("--port", type=int, default=8765)
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--dir", help="demo directory (default ~/.patchquest/demo)")


def _static_dir() -> str | None:
    if os.environ.get("PATCHQUEST_STATIC_DIR"):
        return os.environ["PATCHQUEST_STATIC_DIR"]
    built = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return str(built) if (built / "index.html").is_file() else None


def run(args: argparse.Namespace) -> int:
    from patchquest import demo

    directory = Path(args.dir).expanduser() if args.dir else demo.demo_dir()
    base = f"http://{args.host}:{args.port}"
    if args.action == "path":
        print(directory)
        return 0
    if args.action == "reset":
        try:
            target = demo.reset(directory)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"demo data reset at {target}; `patchquest demo` rebuilds it")
        return 0
    if args.action in ("trigger", "transcript"):
        import httpx

        try:
            reply = httpx.request("POST" if args.action == "trigger" else "GET", f"{base}/api/demo/{'trigger-issue' if args.action == 'trigger' else 'transcript'}", timeout=30)
        except httpx.HTTPError as exc:
            print(f"error: no demo is listening at {base} ({type(exc).__name__}); start one with `patchquest demo`", file=sys.stderr)
            return 1
        print(json.dumps(reply.json(), indent=2))
        return 0 if reply.status_code < 400 else 1

    if not directory.exists():
        demo.reset(directory)
    elif not (directory / demo.MARKER).is_file():
        print(f"error: {directory} exists but is not a demo directory (no {demo.MARKER}); choose another with --dir", file=sys.stderr)
        return 1
    os.environ.update({"PATCHQUEST_DEMO": "1", "PATCHQUEST_DEMO_DIR": str(directory), "PATCHQUEST_DB": str(directory / "patchquest.db"),
                       "PATCHQUEST_HOST": args.host, "PATCHQUEST_PORT": str(args.port)})
    os.environ.pop("PATCHQUEST_API_TOKEN", None)
    static = _static_dir()
    if static:
        os.environ["PATCHQUEST_STATIC_DIR"] = static
    print(f"PatchQuest demo at {base}  (data in {directory})" + ("" if static else "\nnote: no built UI found (run `npm run build` in frontend/); the API still works"),
          file=sys.stderr)
    import uvicorn

    from patchquest.database import set_db_path

    set_db_path(directory / "patchquest.db")
    uvicorn.run("patchquest.main:app", host=args.host, port=args.port, log_level="warning")
    return 0

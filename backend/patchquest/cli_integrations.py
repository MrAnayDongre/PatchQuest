"""``patchquest integrations`` and ``patchquest secrets keygen``. Direct database access, like ``admin``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from patchquest.domain.identity import LOCAL_WORKSPACE_ID


def register(sub: Any) -> None:
    ig = sub.add_parser("integrations", help="connect GitHub, Slack, Linear, Jira, Notion or a generic webhook").add_subparsers(dest="int_cmd", required=True)
    ls = ig.add_parser("list")
    ls.add_argument("--workspace", default=LOCAL_WORKSPACE_ID)
    ls.add_argument("--json", action="store_true")
    kinds = ig.add_parser("kinds", help="what can be connected, and what each needs")
    kinds.add_argument("--json", action="store_true")
    add = ig.add_parser("add")
    add.add_argument("kind")
    add.add_argument("--name")
    add.add_argument("--workspace", default=LOCAL_WORKSPACE_ID)
    add.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", dest="settings", help="a setting; JSON values allow lists: channels='[\"C0123ABC\"]'")
    add.add_argument("--secret-env", action="append", default=[], metavar="NAME=VARIABLE", help="read the secret from this environment variable")
    add.add_argument("--secret", action="append", default=[], metavar="NAME", help="prompt for the value and store it encrypted (needs PATCHQUEST_SECRET_KEY)")
    for verb in ("test", "remove"):
        p = ig.add_parser(verb)
        p.add_argument("id")
        p.add_argument("--workspace", default=LOCAL_WORKSPACE_ID)
    sec = sub.add_parser("secrets", help="encrypted secret storage").add_subparsers(dest="secrets_cmd", required=True)
    sec.add_parser("keygen", help="print a new encryption key (set it as PATCHQUEST_SECRET_KEY; keep it out of the database)")


def _pairs(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep:
            raise ValueError(f"expected KEY=VALUE, got {item!r}")
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def run(args: argparse.Namespace) -> int:
    from getpass import getpass

    from patchquest import secrets_store
    from patchquest.database import get_db
    from patchquest.integrations import service
    from patchquest.integrations.kinds import KINDS
    from patchquest.persistence import identity

    if args.cmd == "secrets":
        try:
            print(secrets_store.generate_key())
        except ImportError:
            print("error: needs the 'cryptography' package: pip install 'patchquest[server]'", file=sys.stderr)
            return 1
        return 0
    actor = identity.local_principal().actor
    try:
        if args.int_cmd == "kinds":
            data = [k.describe() for k in KINDS.values()]
            if args.json:
                print(json.dumps(data, indent=2))
            for k in data if not args.json else []:
                print(f"{k['kind']:<8} {k['title']:<20} secrets: {', '.join(s['name'] for s in k['secrets'])}; config: {', '.join(c['name'] for c in k['config'])}")
            return 0
        with get_db() as conn:
            if args.int_cmd == "list":
                rows = service.list_for(conn, args.workspace)
                if args.json:
                    print(json.dumps(rows, indent=2))
                for r in rows if not args.json else []:
                    print(f"{r['id']}  {r['kind']:<8} {r['name']:<20} {r['status']:<10} {r['webhook_path'] or '-'}"
                          + (f"  ! {r['last_error']}" if r["last_error"] else ""))
                if not rows and not args.json:
                    print("no integrations connected")
                return 0
            if args.int_cmd == "add":
                secrets: dict[str, Any] = {k: {"env": v} for k, v in _pairs(args.secret_env).items()}
                for name in args.secret:
                    secrets[name] = {"value": getpass(f"{name}: ")}
                new_id = service.create(conn, args.workspace, args.kind, args.name or args.kind, _pairs(args.settings), secrets, actor)
                view = service.public_view(service.get(conn, args.workspace, new_id))
                print(f"connected {args.kind} as {new_id}" + (f"; point the sender at {view['webhook_path']}" if view["webhook_path"] else ""))
                return 0
            if args.int_cmd == "test":
                result = service.test_connection(conn, args.workspace, args.id, actor)
                print(("ok: " if result["ok"] else "FAILED: ") + (result["detail"] if result["ok"] else str(result["error"])))
                return 0 if result["ok"] else 1
            service.delete(conn, args.workspace, args.id, actor)
            print("removed")
            return 0
    except (service.IntegrationError, ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

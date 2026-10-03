"""``patchquest plugins ...``: install-time administration of plugins. Direct database access, like ``admin``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from patchquest.domain.failures import PatchQuestError
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.plugins import PluginError
from patchquest.plugins import get_host
from patchquest.plugins.host import PluginApprovalRequired, PluginDenied


def register(sub: Any) -> None:
    pl = sub.add_parser("plugins", help="list, enable, check and call plugins").add_subparsers(dest="plugins_cmd", required=True)
    ls = pl.add_parser("list", help="installed plugins, their trust level, permissions and state")
    ls.add_argument("--json", action="store_true")
    en = pl.add_parser("enable", help="enable a plugin, granting exactly the permissions it declares")
    en.add_argument("name")
    en.add_argument("--grant", action="append", default=[], metavar="PERMISSION", help="repeat for each declared permission")
    en.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="a setting; secrets as KEY=env:VARIABLE")
    dis = pl.add_parser("disable")
    dis.add_argument("name")
    hl = pl.add_parser("health")
    hl.add_argument("name")
    hl.add_argument("--json", action="store_true")
    iv = pl.add_parser("invoke", help="call one capability (for testing; policy applies)")
    iv.add_argument("name")
    iv.add_argument("capability")
    iv.add_argument("--args", default="{}", help="a JSON object")
    iv.add_argument("--approve", action="store_true", help="you approve this call (needed for writes)")
    iv.add_argument("--workspace", default=LOCAL_WORKSPACE_ID)


def _settings(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise PluginError(f"--set expects KEY=VALUE, got {pair!r}")
        try:
            out[key] = json.loads(raw)  # numbers and booleans
        except ValueError:
            out[key] = raw
    return out


def run(args: argparse.Namespace) -> int:
    host = get_host()
    try:
        if args.plugins_cmd == "list":
            rows = host.status()
            if args.json:
                print(json.dumps(rows, indent=2))
            for r in rows if not args.json else []:
                print(f"{r['name']:<20} {r['state']:<12} {r['trust'] or '-':<17} {r['version'] or '-':<8} "
                      f"{','.join(r['permissions']) or 'no permissions'}" + (f"  ! {r['error']}" if r["error"] else ""))
            return 0
        if args.plugins_cmd == "enable":
            host.enable(args.name, grant=args.grant, config=_settings(args.set), actor="cli")
            print(f"enabled {args.name}")
            return 0
        if args.plugins_cmd == "disable":
            host.disable(args.name, actor="cli")
            print(f"disabled {args.name}")
            return 0
        if args.plugins_cmd == "health":
            report = host.health(args.name)
            print(json.dumps(report, indent=2) if args.json else f"{report['name']}: {'ok' if report['ok'] else 'NOT OK'} ({report['state']}) {report['detail']}")
            return 0 if report["ok"] else 1
        from patchquest.runtime import policy as pol

        chain = pol.chain_for(workspace_id=args.workspace)
        result = asyncio.run(host.invoke(args.name, args.capability, json.loads(args.args), chain=chain, approved=args.approve))
        print(json.dumps(result, indent=2))
        return 0
    except (PluginError, PluginDenied, PluginApprovalRequired, PatchQuestError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

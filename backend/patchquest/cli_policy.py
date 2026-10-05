"""``patchquest config explain`` and ``patchquest policy ...``.

Like ``admin`` these read and write the database directly: whoever can run them owns the install. Remote
clients use the policy API, which checks roles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from patchquest.domain.effects import SideEffect
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.policy import PolicyError, Scope

OK, FAILED, USAGE = 0, 1, 64


def register(sub: Any) -> None:
    cfg = sub.add_parser("config", help="inspect the effective configuration").add_subparsers(dest="config_cmd", required=True)
    ex = cfg.add_parser("explain", help="show each setting's value, where it came from and what it overrode")
    ex.add_argument("--key", help="only settings whose name contains this text")
    ex.add_argument("--all", action="store_true", help="include settings still at their built-in default")
    ex.add_argument("--workspace", default=LOCAL_WORKSPACE_ID, help="apply this workspace's policy ceilings")
    ex.add_argument("--json", action="store_true")

    pol = sub.add_parser("policy", help="scoped rules for what runs may do").add_subparsers(dest="policy_cmd", required=True)
    put = pol.add_parser("put", help="validate and store a policy file (YAML or JSON) as its next version")
    put.add_argument("file")
    put.add_argument("--ref", required=True, help="what it attaches to: organisation, workspace, repository path, workflow or user id")
    put.add_argument("--workspace", default=LOCAL_WORKSPACE_ID, help="workspace recorded in the audit log")
    ls = pol.add_parser("list", help="active policies")
    ls.add_argument("--scope", choices=[s.name.lower() for s in Scope if s is not Scope.SYSTEM])
    ls.add_argument("--json", action="store_true")
    off = pol.add_parser("disable", help="stop applying a policy (its history is kept)")
    off.add_argument("name")
    off.add_argument("--scope", required=True, choices=[s.name.lower() for s in Scope if s is not Scope.SYSTEM])
    off.add_argument("--ref", required=True)
    ex2 = pol.add_parser("explain", help="what would be decided for an action, and why")
    ex2.add_argument("action", help="e.g. command.run, action.github.create_pull_request")
    ex2.add_argument("--effect", choices=[e.value for e in SideEffect])
    ex2.add_argument("--workspace", default=LOCAL_WORKSPACE_ID)
    ex2.add_argument("--repo")
    ex2.add_argument("--workflow")
    ex2.add_argument("--user")
    ex2.add_argument("--json", action="store_true")


def _json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def run(args: argparse.Namespace) -> int:
    from patchquest.database import get_db
    from patchquest.persistence import policies
    from patchquest.runtime import policy as rt

    if args.cmd == "config":
        from patchquest.config_explain import explain

        chain = rt.chain_for(workspace_id=args.workspace)
        rows = [s for s in explain(chain=chain) if (args.all or s.source != "default") and (not args.key or args.key in s.key)]
        if args.json:
            _json([s.to_dict() for s in rows])
            return OK
        for s in rows:
            print(f"{s.key} = {s.value}\n    from {s.source}: {s.detail}")
            for layer in s.overridden:
                print(f"    overrode {layer.source} ({layer.detail}): {layer.value}")
        if not rows:
            print("every setting is at its built-in default (use --all to list them)")
        return OK

    if args.policy_cmd == "put":
        try:
            doc = yaml.safe_load(Path(args.file).read_text())
            stored = rt.store(doc, scope_ref=args.ref, actor="cli", workspace_id=args.workspace)
        except (OSError, yaml.YAMLError, PolicyError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return FAILED
        print(f"stored {stored.scope.name.lower()} policy '{stored.name}' version {stored.version} ({stored.digest()})")
        return OK
    with get_db() as conn:
        if args.policy_cmd == "list":
            found = policies.list_policies(conn, Scope.parse(args.scope) if args.scope else None)
            if args.json:
                _json(rt.effective_chain_description(found))
            for p in found if not args.json else []:
                print(f"{p.scope.name.lower():<13} {p.scope_ref:<30} {p.name} v{p.version} {'MALFORMED' if p.is_malformed else ''}".rstrip())
            return OK
        if args.policy_cmd == "disable":
            done = policies.disable(conn, Scope.parse(args.scope), args.ref, args.name)
            print("disabled" if done else "no such active policy", file=sys.stdout if done else sys.stderr)
            return OK if done else FAILED
    chain = rt.chain_for(workspace_id=args.workspace, repo_path=args.repo, workflow_id=args.workflow, user=args.user)
    decision = rt.decide(chain, args.action, SideEffect(args.effect) if args.effect else None)
    if args.json:
        _json({**decision.to_dict(), "policies": rt.effective_chain_description(chain)})
        return OK
    print(f"ACTION    {decision.action}\nDECISION  {decision.result.value}\nREASON    {decision.reason or decision.reason_code}\n"
          f"SOURCE    {decision.source_policy} ({decision.scope.name.lower()} scope)")
    if decision.constraints:
        print(f"LIMITS    {dict(decision.constraints)}")
    return OK

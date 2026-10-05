"""``patchquest memory``, ``preferences``, ``repo`` and ``explain``: look at, and correct, what PatchQuest remembers.

Writes made here are by a person at the keyboard, so they are recorded as ``user_explicit``. Direct database
access, like ``admin``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.memory import MemoryKind, MemoryRefused, Source, Status, to_public
from patchquest.domain.policy import Scope

SCOPES = ["organization", "workspace", "repository", "user", "workflow"]
EXPLAIN_EVENTS = ("decision_explained", "assumption_invalidated", "repository_profile_changed", "memory_selected",
                  "memory_invalidated", "memory_withheld", "memory_unavailable")


def register(sub: Any) -> None:
    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", default=LOCAL_WORKSPACE_ID)
        p.add_argument("--json", action="store_true")

    mem = sub.add_parser("memory", help="what PatchQuest remembers, with where it came from").add_subparsers(dest="memory_cmd", required=True)
    ls = mem.add_parser("list")
    ls.add_argument("--repo", help="include this repository's memories")
    ls.add_argument("--user", help="include this user's memories")
    ls.add_argument("--kind", choices=[k.value for k in MemoryKind])
    ls.add_argument("--all", action="store_true", help="include stale, expired and forgotten records")
    common(ls)
    show = mem.add_parser("show", help="one record with its full provenance and history")
    show.add_argument("id")
    common(show)
    fg = mem.add_parser("forget", help="stop using a record (its history is kept)")
    fg.add_argument("id")
    common(fg)
    add = mem.add_parser("add", help="state a fact yourself")
    add.add_argument("key")
    add.add_argument("value", help="text, or JSON")
    add.add_argument("--scope", choices=SCOPES, default="repository")
    add.add_argument("--ref", help="repository path, user id or workflow id for that scope")
    add.add_argument("--kind", choices=["repository", "procedural", "episodic"], default="repository")
    common(add)

    pr = sub.add_parser("preferences", help="choices that shape what runs do (policy still wins)").add_subparsers(dest="prefs_cmd", required=True)
    pl = pr.add_parser("list", help="each preference's effective value and what decided it")
    pl.add_argument("--repo")
    pl.add_argument("--user")
    common(pl)
    ps = pr.add_parser("set")
    ps.add_argument("key")
    ps.add_argument("value", help="JSON (true, \"auto\", [\"pytest -q\"]) or plain text")
    ps.add_argument("--scope", choices=SCOPES, default="repository")
    ps.add_argument("--ref")
    common(ps)
    pu = pr.add_parser("unset")
    pu.add_argument("key")
    pu.add_argument("--scope", choices=SCOPES, default="repository")
    pu.add_argument("--ref")
    common(pu)

    rp = sub.add_parser("repo", help="what PatchQuest knows about a repository").add_subparsers(dest="repo_cmd", required=True)
    pf = rp.add_parser("profile")
    pf.add_argument("path", nargs="?", default=".")
    pf.add_argument("--refresh", action="store_true", help="re-read the repository first")
    common(pf)
    st = rp.add_parser("set", help="correct a profile field; detection will not replace it")
    st.add_argument("field")
    st.add_argument("value", help="JSON or plain text")
    st.add_argument("--path", default=".")
    common(st)

    ex = sub.add_parser("explain", help="why a run did what it did: the decisions that memory and preferences shaped")
    ex.add_argument("run_id")
    ex.add_argument("--json", action="store_true")


def _value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _print(args: argparse.Namespace, obj: Any, human: str) -> int:
    print(json.dumps(obj, indent=2, default=str) if args.json else human)
    return 0


def run(args: argparse.Namespace) -> int:
    from pathlib import Path

    from patchquest.database import get_db
    from patchquest.persistence import identity as ids
    from patchquest.persistence import memories
    from patchquest.runtime import memory_service as svc
    from patchquest.runtime import repo_profile

    actor = ids.local_principal().actor
    try:
        with get_db() as conn:
            if args.cmd == "explain":
                events = [e for e in conn.execute("SELECT type, phase, message, payload_json FROM run_events WHERE run_id = ? ORDER BY id", (args.run_id,))
                          if e["type"] in EXPLAIN_EVENTS]
                rows = [{"type": e["type"], "phase": e["phase"], "message": e["message"],
                         "payload": json.loads(e["payload_json"]) if e["payload_json"] else None} for e in events]
                return _print(args, rows, "\n".join(f"[{r['type']}] {r['message']}" for r in rows) or "nothing in this run was shaped by memory or preferences")
            owner = svc.owner_for(conn, args.workspace)
            if args.cmd == "memory":
                if args.memory_cmd == "list":
                    repo = str(Path(args.repo).resolve()) if args.repo else None
                    statuses = tuple(Status) if args.all else (Status.ACTIVE,)
                    found = memories.visible(conn, owner, repo=repo, user=args.user, statuses=statuses,
                                             kinds=(MemoryKind(args.kind),) if args.kind else None)
                    return _print(args, [to_public(m) for m in found], "\n".join(
                        f"{m.id[:8]}  {m.scope.name.lower():<12} {m.kind.value:<10} {m.key:<32} {m.source.value:<20} {m.status.value}" for m in found)
                        or "nothing remembered for that scope")
                if args.memory_cmd == "show":
                    found_one = memories.find(conn, owner, args.id)
                    if found_one is None:
                        print("error: no such memory", file=sys.stderr)
                        return 1
                    history = [to_public(m) for m in memories.history(conn, owner, found_one.scope, found_one.scope_id, found_one.key)]
                    return _print(args, {**to_public(found_one), "history": history}, json.dumps({**to_public(found_one), "history": history}, indent=2, default=str))
                if args.memory_cmd == "forget":
                    target = memories.find(conn, owner, args.id)
                    done = target is not None and svc.forget(conn, owner, target.id, actor)
                    print("forgotten" if done else "error: no such active memory", file=sys.stdout if done else sys.stderr)
                    return 0 if done else 1
                scope = Scope.parse(args.scope)
                _, memory = svc.remember(conn, owner, scope=scope, ref=args.ref, key=args.key, value=_value(args.value), kind=MemoryKind(args.kind),
                                         source=Source.USER_EXPLICIT, reason="stated by a person", actor=actor)
                return _print(args, to_public(memory), f"stored {memory.key} (version {memory.version})")
            if args.cmd == "preferences":
                repo = str(Path(args.repo).resolve()) if getattr(args, "repo", None) and args.prefs_cmd == "list" else None
                if args.prefs_cmd == "list":
                    resolved = svc.resolve_preferences(conn, owner, repo=repo, user=args.user or actor)
                    return _print(args, {k: r.to_dict() for k, r in resolved.items()}, "\n".join(
                        f"{k:<40} {r.value!r:<30} <- {r.to_dict()['decided_by'] if r.winner is None else r.winner.scope.name.lower()}" for k, r in resolved.items()))
                scope = Scope.parse(args.scope)
                ref = args.ref or (actor if scope is Scope.USER else None)
                if args.prefs_cmd == "set":
                    m = svc.set_preference(conn, owner, scope=scope, ref=ref, key=args.key, value=_value(args.value), actor=actor)
                    return _print(args, to_public(m), f"{args.key} = {m.value!r} at {scope.name.lower()} scope")
                done = svc.clear_preference(conn, owner, scope=scope, ref=ref, key=args.key, actor=actor)
                print("cleared" if done else "nothing to clear")
                return 0
            repo = str(Path(getattr(args, "path", ".")).resolve())
            if args.repo_cmd == "set":
                m = repo_profile.override(conn, owner, repo, args.field, _value(args.value), actor)
                return _print(args, to_public(m), f"{args.field} set; detection will not replace it")
            change = repo_profile.refresh(conn, owner, repo, force=True) if args.refresh or not repo_profile.profile(conn, owner, repo) else None
            data = repo_profile.profile(conn, owner, repo)
            text = "\n".join(f"{k:<20} {json.dumps(v['value'])}\n    {v['source']}, confidence {v['confidence']}, verified {v['last_verified']}"
                             for k, v in data.items()) or "no profile yet"
            return _print(args, {"profile": data, "refresh": change.to_dict() if change else None}, text)
    except (MemoryRefused, ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

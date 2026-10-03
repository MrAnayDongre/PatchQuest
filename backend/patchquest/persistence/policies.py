"""Stored policies: versioned documents attached to a scope, and the chain that applies to a run."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from patchquest.domain.policy import Policy, PolicyError, Scope, malformed, parse_policy
from patchquest.persistence.ledger import now_iso


def put(conn: sqlite3.Connection, doc: Mapping[str, Any], *, scope_ref: str, actor: str) -> Policy:
    """Validate ``doc`` and store it as the next version of (scope, scope_ref, name). Raises ``PolicyError``."""
    policy = parse_policy(doc, scope_ref=scope_ref)
    if not scope_ref:
        raise PolicyError("a policy must say what it is attached to (organisation, workspace, repository, workflow or user id)")
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM policies WHERE scope = ? AND scope_ref = ? AND name = ?",
                       (int(policy.scope), scope_ref, policy.name)).fetchone()
    version = int(row[0]) + 1
    conn.execute("UPDATE policies SET active = 0 WHERE scope = ? AND scope_ref = ? AND name = ?",
                 (int(policy.scope), scope_ref, policy.name))
    conn.execute("INSERT INTO policies (scope, scope_ref, name, version, document_json, active, created_at, created_by) "
                 "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                 (int(policy.scope), scope_ref, policy.name, version, json.dumps(doc, sort_keys=True), now_iso(), actor))
    return parse_policy(doc, scope_ref=scope_ref, version=version)


def disable(conn: sqlite3.Connection, scope: Scope, scope_ref: str, name: str) -> bool:
    return conn.execute("UPDATE policies SET active = 0 WHERE scope = ? AND scope_ref = ? AND name = ? AND active = 1",
                        (int(scope), scope_ref, name)).rowcount > 0


def _load(row: sqlite3.Row) -> Policy:
    scope = Scope(row["scope"])
    try:
        return parse_policy(json.loads(row["document_json"]), scope_ref=row["scope_ref"], version=row["version"])
    except (ValueError, PolicyError) as exc:  # a corrupt row must deny, not vanish
        return malformed(row["name"], scope, row["scope_ref"], str(exc))


def list_policies(conn: sqlite3.Connection, scope: Scope | None = None, scope_ref: str | None = None) -> list[Policy]:
    sql = "SELECT * FROM policies WHERE active = 1"
    params: list[Any] = []
    if scope is not None:
        sql, params = sql + " AND scope = ?", [int(scope)]
    if scope_ref is not None:
        sql, params = sql + " AND scope_ref = ?", [*params, scope_ref]
    return [_load(r) for r in conn.execute(sql + " ORDER BY scope, scope_ref, name", params)]


def load_chain(conn: sqlite3.Connection, *, workspace_id: str, repo_path: str | None = None,
               workflow_id: str | None = None, user: str | None = None) -> list[Policy]:
    """Every active policy that applies, widest scope first. The organisation comes from the workspace."""
    org = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    wanted = [(Scope.ORGANIZATION, org["org_id"] if org else None), (Scope.WORKSPACE, workspace_id),
              (Scope.REPOSITORY, repo_path), (Scope.WORKFLOW, workflow_id), (Scope.USER, user)]
    chain: list[Policy] = []
    for scope, ref in wanted:
        if ref:
            chain += list_policies(conn, scope, ref)
    return chain

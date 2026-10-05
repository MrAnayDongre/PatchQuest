"""Organisations, workspaces, principals, memberships, API tokens and the security audit log."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from patchquest.domain.identity import LOCAL_ORG_ID, LOCAL_WORKSPACE_ID, Principal, Role
from patchquest.persistence.ledger import now_iso

TOKEN_PREFIX = "pq_"  # noqa: S105 - a public marker so secret scanners recognise a leaked token, not a secret


class UnknownToken(PermissionError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_org(conn: sqlite3.Connection, name: str) -> str:
    org_id = _new_id("org")
    conn.execute("INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)", (org_id, name, now_iso()))
    return org_id


def create_workspace(conn: sqlite3.Connection, org_id: str, name: str) -> str:
    ws_id = _new_id("ws")
    conn.execute("INSERT INTO workspaces (id, org_id, name, created_at) VALUES (?, ?, ?, ?)", (ws_id, org_id, name, now_iso()))
    return ws_id


def create_principal(conn: sqlite3.Connection, org_id: str, name: str, kind: str = "user") -> str:
    if kind not in ("user", "service"):
        raise ValueError(f"unknown principal kind: {kind}")
    pid = _new_id("p")
    conn.execute("INSERT INTO principals (id, org_id, kind, name, created_at) VALUES (?, ?, ?, ?, ?)",
                 (pid, org_id, kind, name, now_iso()))
    return pid


def set_role(conn: sqlite3.Connection, principal_id: str, workspace_id: str, role: Role) -> None:
    principal = conn.execute("SELECT org_id FROM principals WHERE id = ?", (principal_id,)).fetchone()
    workspace = conn.execute("SELECT org_id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if principal is None or workspace is None:
        raise LookupError("unknown principal or workspace")
    if principal["org_id"] != workspace["org_id"]:
        raise ValueError("a principal can only be a member of workspaces in its own organisation")
    conn.execute("INSERT INTO memberships (principal_id, workspace_id, role) VALUES (?, ?, ?) "
                 "ON CONFLICT(principal_id, workspace_id) DO UPDATE SET role = excluded.role",
                 (principal_id, workspace_id, role.value))


def issue_token(conn: sqlite3.Connection, principal_id: str, label: str = "", expires_in_days: float | None = None) -> tuple[str, str]:
    """Create a token for ``principal_id``. Returns ``(token_id, secret)``; the secret is shown once and never stored."""
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_id = _new_id("tok")
    expires = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
    conn.execute("INSERT INTO api_tokens (id, principal_id, token_hash, prefix, label, created_at, expires_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (token_id, principal_id, hash_token(secret), secret[: len(TOKEN_PREFIX) + 4], label, now_iso(), expires))
    return token_id, secret


def revoke_token(conn: sqlite3.Connection, token_id: str) -> bool:
    return conn.execute("UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                        (now_iso(), token_id)).rowcount == 1


def load_principal(conn: sqlite3.Connection, principal_id: str) -> Principal | None:
    row = conn.execute("SELECT id, org_id, kind, name FROM principals WHERE id = ? AND disabled = 0", (principal_id,)).fetchone()
    if row is None:
        return None
    direct = {m["workspace_id"]: Role(m["role"]) for m in conn.execute(
        "SELECT workspace_id, role FROM memberships WHERE principal_id = ?", (principal_id,))}
    from patchquest.persistence import tenancy

    via_teams = tenancy.team_roles_for(conn, principal_id)
    roles = {}
    for ws in direct.keys() | via_teams.keys():
        best = tenancy.effective_role(direct.get(ws), via_teams.get(ws, []))
        if best is not None:
            roles[ws] = best
    return Principal(row["id"], row["kind"], row["name"], row["org_id"], roles)


def authenticate(conn: sqlite3.Connection, secret: str) -> Principal:
    """Resolve a bearer token. Raises ``UnknownToken`` for unknown, revoked, expired or disabled-principal tokens."""
    row = conn.execute("SELECT id, principal_id, expires_at, revoked_at FROM api_tokens WHERE token_hash = ?",
                       (hash_token(secret),)).fetchone()
    if row is None or row["revoked_at"] or (row["expires_at"] and row["expires_at"] <= now_iso()):
        raise UnknownToken("invalid, revoked or expired token")
    principal = load_principal(conn, row["principal_id"])
    if principal is None:
        raise UnknownToken("invalid, revoked or expired token")
    conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (now_iso(), row["id"]))
    return principal


def has_tokens(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM api_tokens WHERE revoked_at IS NULL LIMIT 1").fetchone() is not None


def workspace_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0])


def local_principal() -> Principal:
    """The implicit owner of single-user local mode, where no authentication is configured."""
    return Principal("local", "local", "local user", LOCAL_ORG_ID, {LOCAL_WORKSPACE_ID: Role.OWNER})


def list_tokens(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT t.id, t.prefix, t.label, t.created_at, t.expires_at, t.revoked_at, t.last_used_at, "
                        "p.name AS principal, p.kind FROM api_tokens t JOIN principals p ON p.id = t.principal_id "
                        "ORDER BY t.created_at").fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def audit(conn: sqlite3.Connection, action: str, *, actor: str, outcome: str = "ok", workspace_id: str | None = None,
          org_id: str | None = None, target: str | None = None, detail: dict[str, Any] | None = None,
          remote: str | None = None) -> None:
    """Append to the security audit log (who did what to what, and whether it was allowed). Never store secrets in ``detail``."""
    conn.execute("INSERT INTO audit_log (ts, org_id, workspace_id, actor, action, target, outcome, detail_json, remote) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (now_iso(), org_id, workspace_id, actor, action, target, outcome,
                  json.dumps(detail, default=str) if detail else None, remote))


def read_audit(conn: sqlite3.Connection, workspace_ids: list[str] | None, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    """Audit entries visible to the caller: those in ``workspace_ids`` (None: all)."""
    sql = "SELECT * FROM audit_log WHERE id > ?"
    params: list[Any] = [after]
    if workspace_ids is not None:
        sql += f" AND workspace_id IN ({','.join('?' * len(workspace_ids)) or 'NULL'})"
        params += workspace_ids
    rows = conn.execute(sql + " ORDER BY id LIMIT ?", [*params, limit]).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["detail"] = json.loads(d.pop("detail_json")) if d.get("detail_json") else None
        out.append(d)
    return out

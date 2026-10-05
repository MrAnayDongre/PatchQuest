"""Storage for memories: tenant-owned rows, versioned by supersession, never deleted.

Every row says which tenant owns it (``org_id`` for organisation scope, ``workspace_id`` otherwise); every read
is filtered by that owner in SQL, so a caller can only ever see its own tenant's records.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from patchquest.domain.memory import (
    AUTHORITY,
    DEFAULT_CONFIDENCE,
    Memory,
    MemoryKind,
    MemoryRefused,
    Source,
    Status,
    WriteOutcome,
    check_write,
    expiry,
    utcnow,
)
from patchquest.domain.policy import Scope
from patchquest.tools.secret_guard import has_secrets


@dataclass(frozen=True)
class Owner:
    """The tenant a caller acts for."""

    org_id: str
    workspace_id: str


def _columns(owner: Owner, scope: Scope) -> tuple[str | None, str | None]:
    return (owner.org_id, None) if scope is Scope.ORGANIZATION else (owner.org_id, owner.workspace_id)


def _row(r: sqlite3.Row) -> Memory:
    return Memory(
        id=r["id"], kind=MemoryKind(r["kind"]), scope=Scope[r["scope"].upper()], scope_id=r["scope_id"], key=r["key"],
        value=json.loads(r["value_json"]), source=Source(r["source"]), reason=r["reason"], authored_by=r["authored_by"],
        confidence=r["confidence"], status=Status(r["status"]), version=r["version"],
        created_at=datetime.fromisoformat(r["created_at"]), updated_at=datetime.fromisoformat(r["updated_at"]),
        last_verified_at=datetime.fromisoformat(r["last_verified_at"]),
        expires_at=datetime.fromisoformat(r["expires_at"]) if r["expires_at"] else None,
        evidence=json.loads(r["evidence_json"] or "{}"), metadata=json.loads(r["metadata_json"] or "{}"),
        org_id=r["org_id"], workspace_id=r["workspace_id"], supersedes=r["supersedes"])


def _owned(owner: Owner, scope: Scope) -> tuple[str, list[Any]]:
    if scope is Scope.ORGANIZATION:
        return "org_id = ? AND workspace_id IS NULL", [owner.org_id]
    return "org_id = ? AND workspace_id = ?", [owner.org_id, owner.workspace_id]


def active(conn: sqlite3.Connection, owner: Owner, kind: MemoryKind, scope: Scope, scope_id: str, key: str) -> Memory | None:
    where, params = _owned(owner, scope)
    row = conn.execute(f"SELECT * FROM memories WHERE {where} AND kind = ? AND scope = ? AND scope_id = ? AND key = ? AND status = 'active'",
                       [*params, kind.value, scope.name.lower(), scope_id, key]).fetchone()
    return _row(row) if row else None


def put(conn: sqlite3.Connection, owner: Owner, *, kind: MemoryKind, scope: Scope, scope_id: str, key: str, value: Any,
        source: Source, reason: str, authored_by: str, confidence: float | None = None, ttl: timedelta | None = None,
        evidence: Mapping[str, str] | None = None, metadata: Mapping[str, Any] | None = None,
        now: datetime | None = None) -> tuple[WriteOutcome, Memory]:
    """Store ``value``. Raises ``MemoryRefused``. A lower-authority source never replaces a higher one."""
    text = json.dumps(value, sort_keys=True, default=str)
    check_write(kind, scope, source, key, text)
    if has_secrets(text):
        raise MemoryRefused("this looks like it contains a secret; secrets are never stored as memory")
    if not scope_id:
        raise MemoryRefused("a memory must say which organisation, workspace, repository, user or workflow owns it")
    now = now or utcnow()
    existing = active(conn, owner, kind, scope, scope_id, key)
    conf = DEFAULT_CONFIDENCE[source] if confidence is None else max(0.0, min(1.0, confidence))
    org_id, workspace_id = _columns(owner, scope)

    if existing is not None and json.dumps(existing.value, sort_keys=True, default=str) == text:
        higher = AUTHORITY[source] >= AUTHORITY[existing.source]
        new_conf = min(1.0, max(existing.confidence, conf) + 0.05) if higher else existing.confidence
        conn.execute("UPDATE memories SET confidence = ?, last_verified_at = ?, updated_at = ?, expires_at = ?, evidence_json = ? WHERE id = ?",
                     (new_conf, now.isoformat(), now.isoformat(), _iso(expiry(existing.source, now, ttl)),
                      json.dumps(dict(evidence or existing.evidence)), existing.id))
        return WriteOutcome.REINFORCED, _get(conn, existing.id)
    if existing is not None and AUTHORITY[source] < AUTHORITY[existing.source]:
        return WriteOutcome.KEPT_EXISTING, existing

    version = 1
    if existing is not None:
        version = existing.version + 1
        conn.execute("UPDATE memories SET status = 'superseded', updated_at = ? WHERE id = ?", (now.isoformat(), existing.id))
    new_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO memories (id, kind, scope, scope_id, org_id, workspace_id, key, value_json, source, reason, authored_by, confidence, "
        "status, version, created_at, updated_at, last_verified_at, expires_at, evidence_json, metadata_json, supersedes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id, kind.value, scope.name.lower(), scope_id, org_id, workspace_id, key, text, source.value, reason[:300], authored_by[:120],
         conf, version, now.isoformat(), now.isoformat(), now.isoformat(), _iso(expiry(source, now, ttl)),
         json.dumps(dict(evidence or {})), json.dumps(dict(metadata or {})), existing.id if existing else None))
    return (WriteOutcome.UPDATED if existing else WriteOutcome.CREATED), _get(conn, new_id)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _get(conn: sqlite3.Connection, memory_id: str) -> Memory:
    return _row(conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone())


def get(conn: sqlite3.Connection, owner: Owner, memory_id: str) -> Memory | None:
    """A record by id, only if this tenant owns it (a foreign id is indistinguishable from a missing one)."""
    row = conn.execute("SELECT * FROM memories WHERE id = ? AND org_id = ? AND (workspace_id IS NULL OR workspace_id = ?)",
                       (memory_id, owner.org_id, owner.workspace_id)).fetchone()
    return _row(row) if row else None


def visible(conn: sqlite3.Connection, owner: Owner, *, repo: str | None = None, user: str | None = None, workflow: str | None = None,
            kinds: tuple[MemoryKind, ...] | None = None, statuses: tuple[Status, ...] = (Status.ACTIVE,)) -> list[Memory]:
    """Every record this tenant may use for a run on ``repo`` by ``user`` in ``workflow``: organisation, workspace and,
    when named, repository, user and workflow scope. Nothing from another tenant, repository or user."""
    clauses = ["(scope = 'organization' AND org_id = ? AND scope_id = ? AND workspace_id IS NULL)",
               "(org_id = ? AND workspace_id = ? AND scope = 'workspace' AND scope_id = ?)"]
    params: list[Any] = [owner.org_id, owner.org_id, owner.org_id, owner.workspace_id, owner.workspace_id]
    for scope, ref in (("repository", repo), ("user", user), ("workflow", workflow)):
        if ref:
            clauses.append(f"(org_id = ? AND workspace_id = ? AND scope = '{scope}' AND scope_id = ?)")
            params += [owner.org_id, owner.workspace_id, ref]
    sql = f"SELECT * FROM memories WHERE ({' OR '.join(clauses)}) AND status IN ({','.join('?' * len(statuses))})"
    params += [s.value for s in statuses]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params += [k.value for k in kinds]
    return [_row(r) for r in conn.execute(sql + " ORDER BY scope, key, version DESC", params)]


def set_status(conn: sqlite3.Connection, memory_id: str, status: Status, now: datetime | None = None) -> None:
    conn.execute("UPDATE memories SET status = ?, updated_at = ? WHERE id = ?", (status.value, (now or utcnow()).isoformat(), memory_id))


def forget(conn: sqlite3.Connection, owner: Owner, memory_id: str) -> Memory | None:
    found = get(conn, owner, memory_id)
    if found is None or (found.status is not Status.ACTIVE and found.status is not Status.STALE):
        return None
    set_status(conn, memory_id, Status.FORGOTTEN)
    return _row(conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone())


def expire_due(conn: sqlite3.Connection, owner: Owner, now: datetime | None = None) -> list[Memory]:
    """Mark active records past their expiry as expired (lazily, whenever they are looked at). Returns those changed."""
    now = now or utcnow()
    rows = conn.execute("SELECT * FROM memories WHERE org_id = ? AND (workspace_id IS NULL OR workspace_id = ?) AND status = 'active' "
                        "AND expires_at IS NOT NULL AND expires_at <= ?", (owner.org_id, owner.workspace_id, now.isoformat())).fetchall()
    for r in rows:
        set_status(conn, r["id"], Status.EXPIRED, now)
    return [_row(r) for r in rows]


def history(conn: sqlite3.Connection, owner: Owner, scope: Scope, scope_id: str, key: str) -> list[Memory]:
    where, params = _owned(owner, scope)
    return [_row(r) for r in conn.execute(
        f"SELECT * FROM memories WHERE {where} AND scope = ? AND scope_id = ? AND key = ? ORDER BY version", [*params, scope.name.lower(), scope_id, key])]


def find(conn: sqlite3.Connection, owner: Owner, id_or_prefix: str) -> Memory | None:
    """A record by full id or unambiguous id prefix, within this tenant only."""
    if len(id_or_prefix) < 4:
        return None
    rows = conn.execute("SELECT * FROM memories WHERE id LIKE ? ESCAPE '\\' AND org_id = ? AND (workspace_id IS NULL OR workspace_id = ?) LIMIT 2",
                        (id_or_prefix.replace("\\", "").replace("%", "").replace("_", "") + "%", owner.org_id, owner.workspace_id)).fetchall()
    return _row(rows[0]) if len(rows) == 1 else None

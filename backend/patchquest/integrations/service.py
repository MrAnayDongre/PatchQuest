"""Creating, inspecting and using a workspace's integrations.

One integration per connector kind per workspace (so a workflow action named ``slack.post_message`` has exactly one
meaning there). Non-secret configuration is validated against the kind's schema; secrets are never returned - only
which ones are set and whether each is an environment reference or an encrypted stored value.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from typing import Any

from patchquest import secrets_store
from patchquest.connectors.base import Connector, SecretRef
from patchquest.connectors.ssrf import SafeHttp
from patchquest.database import get_db
from patchquest.integrations.kinds import KINDS, ConfigError, Kind, validate_config
from patchquest.persistence import identity
from patchquest.persistence.ledger import now_iso
from patchquest.workflows.connector_backend import ConnectorBackend

_ENV = r"[A-Z_][A-Z0-9_]{0,63}"
_cache: dict[tuple[str, str], Connector] = {}
_cache_lock = threading.Lock()
_http_override: SafeHttp | None = None  # tests inject a mock transport; production uses the SSRF-guarded default


def set_http(http: SafeHttp | None) -> None:
    global _http_override
    _http_override = http
    with _cache_lock:
        _cache.clear()


class IntegrationError(ValueError):
    """A request about an integration was refused. The message is safe to show."""


def kind_of(name: str) -> Kind:
    try:
        return KINDS[name]
    except KeyError:
        raise IntegrationError(f"unknown integration kind '{name}' (one of: {', '.join(sorted(KINDS))})") from None


def _refs(conn: sqlite3.Connection, workspace_id: str, integration_id: str, kind: Kind, secrets: dict[str, Any], actor: str,
          existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the secret inputs, store any values encrypted, and return the reference map to persist."""
    import re

    refs: dict[str, Any] = dict(existing or {})
    allowed = set(kind.secrets) | set(kind.optional_secrets)
    if unknown := set(secrets) - allowed:
        raise IntegrationError(f"unknown secret(s) for {kind.name}: {', '.join(sorted(unknown))}")
    for name, given in secrets.items():
        if not isinstance(given, dict) or len(given) != 1 or next(iter(given)) not in ("env", "value"):
            raise IntegrationError(f"secret '{name}' must be {{\"env\": \"VARIABLE\"}} or {{\"value\": \"...\"}}")
        how, content = next(iter(given.items()))
        if how == "env":
            if not isinstance(content, str) or not re.fullmatch(_ENV, content):
                raise IntegrationError(f"secret '{name}': '{content}' is not a valid environment variable name")
            refs[name] = {"kind": "env", "name": content}
            conn.execute("DELETE FROM secrets WHERE workspace_id = ? AND owner_id = ? AND name = ?", (workspace_id, integration_id, name))
        else:
            try:
                secrets_store.put(conn, workspace_id, integration_id, name, str(content), actor)
            except secrets_store.SecretsUnavailable as exc:
                raise IntegrationError(str(exc)) from None
            except ValueError as exc:
                raise IntegrationError(f"secret '{name}': {exc}") from None
            refs[name] = {"kind": "stored"}
    missing = [n for n in kind.secrets if n not in refs]
    if missing:
        raise IntegrationError(f"missing secret(s): {', '.join(missing)}")
    return refs


def create(conn: sqlite3.Connection, workspace_id: str, kind_name: str, name: str, config: dict[str, Any], secrets: dict[str, Any],
           actor: str) -> str:
    kind = kind_of(kind_name)
    if not name.strip() or len(name) > 64:
        raise IntegrationError("a name of 1-64 characters is required")
    try:
        clean = validate_config(kind, config)
    except ConfigError as exc:
        raise IntegrationError(str(exc)) from None
    if conn.execute("SELECT 1 FROM integrations WHERE workspace_id = ? AND kind = ?", (workspace_id, kind.name)).fetchone():
        raise IntegrationError(f"this workspace already has a {kind.title} integration; edit or remove it first")
    integration_id = "int_" + uuid.uuid4().hex[:16]
    refs = _refs(conn, workspace_id, integration_id, kind, secrets, actor)
    _build_check(workspace_id, integration_id, kind, clean, refs)
    now = now_iso()
    conn.execute("INSERT INTO integrations (id, workspace_id, kind, name, config_json, secret_refs_json, created_by, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (integration_id, workspace_id, kind.name, name.strip(), json.dumps(clean), json.dumps(refs), actor, now, now))
    identity.audit(conn, "integration.create", actor=actor, workspace_id=workspace_id, target=integration_id,
                   detail={"kind": kind.name, "secrets": {n: r["kind"] for n, r in refs.items()}})
    return integration_id


def update(conn: sqlite3.Connection, workspace_id: str, integration_id: str, actor: str, *, name: str | None = None,
           config: dict[str, Any] | None = None, secrets: dict[str, Any] | None = None, enabled: bool | None = None) -> None:
    row = _row(conn, workspace_id, integration_id)
    kind = kind_of(row["kind"])
    new_config = row["config"]
    if config is not None:
        try:
            new_config = validate_config(kind, config)
        except ConfigError as exc:
            raise IntegrationError(str(exc)) from None
    refs = _refs(conn, workspace_id, integration_id, kind, secrets or {}, actor, existing=row["secret_refs"]) if secrets else row["secret_refs"]
    _build_check(workspace_id, integration_id, kind, new_config, refs)
    status = row["status"] if enabled is None else ("connected" if enabled else "disabled")
    conn.execute("UPDATE integrations SET name = ?, config_json = ?, secret_refs_json = ?, status = ?, updated_at = ? WHERE id = ?",
                 ((name or row["name"]).strip(), json.dumps(new_config), json.dumps(refs), status, now_iso(), integration_id))
    identity.audit(conn, "integration.update", actor=actor, workspace_id=workspace_id, target=integration_id,
                   detail={"config_changed": config is not None, "secrets_changed": sorted(secrets or {}), "status": status})
    with _cache_lock:
        _cache.pop((integration_id, row["updated_at"]), None)


def delete(conn: sqlite3.Connection, workspace_id: str, integration_id: str, actor: str) -> None:
    _row(conn, workspace_id, integration_id)
    secrets_store.delete_owner(conn, workspace_id, integration_id)
    conn.execute("DELETE FROM integrations WHERE id = ? AND workspace_id = ?", (integration_id, workspace_id))
    identity.audit(conn, "integration.delete", actor=actor, workspace_id=workspace_id, target=integration_id)


def _row(conn: sqlite3.Connection, workspace_id: str, integration_id: str) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM integrations WHERE id = ? AND workspace_id = ?", (integration_id, workspace_id)).fetchone()
    if r is None:
        raise LookupError(integration_id)
    d = {k: r[k] for k in r.keys()}
    d["config"], d["secret_refs"] = json.loads(d.pop("config_json")), json.loads(d.pop("secret_refs_json"))
    return d


def get(conn: sqlite3.Connection, workspace_id: str, integration_id: str) -> dict[str, Any]:
    return _row(conn, workspace_id, integration_id)


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    """What may be shown: never a secret, only how each one is supplied."""
    return {"id": row["id"], "kind": row["kind"], "name": row["name"], "config": row["config"], "status": row["status"],
            "secrets": {n: ({"env": r["name"]} if r["kind"] == "env" else {"stored": True}) for n, r in row["secret_refs"].items()},
            "created_at": row["created_at"], "updated_at": row["updated_at"], "last_checked_at": row["last_checked_at"],
            "last_error": row["last_error"], "webhook_path": f"/hooks/{row['id']}" if kind_of(row["kind"]).inbound else None}


def list_for(conn: sqlite3.Connection, workspace_id: str) -> list[dict[str, Any]]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM integrations WHERE workspace_id = ? ORDER BY kind", (workspace_id,))]
    return [public_view(_row(conn, workspace_id, i)) for i in ids]


# ------------------------------------------------------------------ building connectors
def _secret_refs(workspace_id: str, integration_id: str, refs: dict[str, Any]) -> dict[str, SecretRef]:
    out: dict[str, SecretRef] = {}
    for name, ref in refs.items():
        out[name] = (SecretRef(ref["name"]) if ref["kind"] == "env"
                     else secrets_store.StoredSecretRef(name, workspace_id=workspace_id, owner_id=integration_id))
    return out


def _build(workspace_id: str, integration_id: str, kind: Kind, config: dict[str, Any], refs: dict[str, Any]) -> Connector:
    try:
        return kind.build(workspace_id, config, _secret_refs(workspace_id, integration_id, refs), _http_override)
    except ValueError as exc:
        raise IntegrationError(str(exc)) from None


def _build_check(workspace_id: str, integration_id: str, kind: Kind, config: dict[str, Any], refs: dict[str, Any]) -> None:
    _build(workspace_id, integration_id, kind, config, refs)


def connector_for(row: dict[str, Any]) -> Connector:
    key = (row["id"], row["updated_at"])
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    connector = _build(row["workspace_id"], row["id"], kind_of(row["kind"]), row["config"], row["secret_refs"])
    with _cache_lock:
        _cache[key] = connector
    return connector


def find_enabled(workspace_id: str, kind_name: str) -> dict[str, Any] | None:
    with get_db() as conn:
        r = conn.execute("SELECT id FROM integrations WHERE workspace_id = ? AND kind = ? AND status = 'connected'", (workspace_id, kind_name)).fetchone()
        return _row(conn, workspace_id, r["id"]) if r else None


def backend_for(workspace_id: str, kind_name: str) -> ConnectorBackend | None:
    """The workflow-engine backend for this workspace's enabled integration of that kind, or None."""
    row = find_enabled(workspace_id, kind_name)
    if row is None:
        return None
    return ConnectorBackend(connector_for(row), kind_of(kind_name).actions)


def test_connection(conn: sqlite3.Connection, workspace_id: str, integration_id: str, actor: str) -> dict[str, Any]:
    """Run the kind's read-only check. The result (never a secret) is recorded on the integration."""
    row = _row(conn, workspace_id, integration_id)
    try:
        detail = connector_for(row).health()
        ok, error = True, None
    except Exception as exc:  # any failure is the answer, not a crash
        from patchquest.domain.failures import Origin, classify

        failure = classify(exc, origin=Origin.CONNECTOR)
        ok, detail, error = False, failure.spec.message, f"{failure.kind.value}: {failure.detail}"[:300]
    status = "disabled" if row["status"] == "disabled" else ("connected" if ok else "error")
    conn.execute("UPDATE integrations SET last_checked_at = ?, last_error = ?, status = ? WHERE id = ?", (now_iso(), error, status, integration_id))
    return {"ok": ok, "detail": detail, "error": error}

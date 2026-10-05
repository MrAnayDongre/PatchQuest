"""Encrypted secrets for integrations: write-only through the API, bound to the row they belong to.

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) under a key that lives only in the environment
(``PATCHQUEST_SECRET_KEY``; ``PATCHQUEST_SECRET_KEY_PREVIOUS`` may list older keys, comma separated, so a key can be
rotated). The key is never stored beside the data. The plaintext that is encrypted carries the workspace, owner and
name it belongs to and is checked on decryption, so copying a ciphertext into another row (or another tenant's)
does not yield a usable secret. Without a key, storing is refused; ``env:`` references still work everywhere.

Not provided: hardware-backed keys, per-tenant keys, or protection from someone who can read both the environment of
the server process and the database.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from patchquest.connectors.base import MissingCredential, SecretRef
from patchquest.database import get_db
from patchquest.persistence.ledger import now_iso

KEY_ENV = "PATCHQUEST_SECRET_KEY"
PREVIOUS_ENV = "PATCHQUEST_SECRET_KEY_PREVIOUS"
MAX_SECRET_BYTES = 8192


class SecretsUnavailable(RuntimeError):
    """No usable encryption key (or the cryptography package is missing). The message says how to fix it."""


def generate_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _fernet(environ: dict[str, str] | None = None) -> Any:
    env = os.environ if environ is None else environ
    try:
        from cryptography.fernet import Fernet, InvalidToken, MultiFernet  # noqa: F401
    except ImportError:
        raise SecretsUnavailable("storing secrets needs the 'cryptography' package: pip install 'patchquest[server]'") from None
    current = env.get(KEY_ENV, "").strip()
    if not current:
        raise SecretsUnavailable(f"set {KEY_ENV} to a key from `patchquest secrets keygen` to store secrets "
                                 "(or reference an environment variable instead)")
    keys = [current, *[k.strip() for k in env.get(PREVIOUS_ENV, "").split(",") if k.strip()]]
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except ValueError:
        raise SecretsUnavailable(f"{KEY_ENV} is not a valid key; generate one with `patchquest secrets keygen`") from None


def available() -> bool:
    try:
        _fernet()
        return True
    except SecretsUnavailable:
        return False


def put(conn: sqlite3.Connection, workspace_id: str, owner_id: str, name: str, value: str, actor: str,
        environ: dict[str, str] | None = None) -> None:
    if not value or len(value.encode()) > MAX_SECRET_BYTES:
        raise ValueError(f"a secret must be 1-{MAX_SECRET_BYTES} bytes")
    token = _fernet(environ).encrypt(json.dumps({"w": workspace_id, "o": owner_id, "n": name, "v": value}).encode()).decode()
    conn.execute(
        "INSERT INTO secrets (workspace_id, owner_id, name, ciphertext, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(workspace_id, owner_id, name) DO UPDATE SET ciphertext = excluded.ciphertext, rotated_at = ?, created_by = excluded.created_by",
        (workspace_id, owner_id, name, token, actor, now_iso(), now_iso()))


def get(conn: sqlite3.Connection, workspace_id: str, owner_id: str, name: str, environ: dict[str, str] | None = None) -> str:
    from cryptography.fernet import InvalidToken

    row = conn.execute("SELECT ciphertext FROM secrets WHERE workspace_id = ? AND owner_id = ? AND name = ?",
                       (workspace_id, owner_id, name)).fetchone()
    if row is None:
        raise KeyError(name)
    try:
        data = json.loads(_fernet(environ).decrypt(row["ciphertext"].encode()))
    except InvalidToken:
        raise SecretsUnavailable("a stored secret cannot be decrypted with the configured key(s)") from None
    if (data.get("w"), data.get("o"), data.get("n")) != (workspace_id, owner_id, name):
        raise SecretsUnavailable("a stored secret does not belong to the place it was found")  # a copied ciphertext
    conn.execute("UPDATE secrets SET last_used_at = ? WHERE workspace_id = ? AND owner_id = ? AND name = ?",
                 (now_iso(), workspace_id, owner_id, name))
    return str(data["v"])


def names(conn: sqlite3.Connection, workspace_id: str, owner_id: str) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM secrets WHERE workspace_id = ? AND owner_id = ? ORDER BY name",
                                            (workspace_id, owner_id))]


def delete_owner(conn: sqlite3.Connection, workspace_id: str, owner_id: str) -> int:
    return conn.execute("DELETE FROM secrets WHERE workspace_id = ? AND owner_id = ?", (workspace_id, owner_id)).rowcount


@dataclass(frozen=True)
class StoredSecretRef(SecretRef):
    """A reference to an encrypted secret; resolved when used, never held as a value."""

    workspace_id: str = ""
    owner_id: str = ""

    def resolve(self, environ: Any = None) -> str:
        try:
            with get_db() as conn:
                return get(conn, self.workspace_id, self.owner_id, self.name)
        except KeyError:
            raise MissingCredential(self) from None
        except SecretsUnavailable as exc:
            from patchquest.domain.failures import FailureKind, PatchQuestError

            raise PatchQuestError(FailureKind.ENVIRONMENT_FAILURE, str(exc)) from None

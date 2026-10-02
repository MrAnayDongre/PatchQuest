"""API boundary security: authentication, Host validation and repository path policy.

PatchQuest executes model-chosen commands against source trees, so whoever can call the API
can run code as the user. The defaults are therefore:

* bind to loopback only; refuse to start on any other interface without a token
* if a token is configured, every route except ``/api/health`` requires it (Bearer header;
  SSE endpoints also accept ``?token=`` because EventSource cannot set headers)
* reject unexpected ``Host`` headers (DNS-rebinding defence)
* runs may only target an existing directory that is not a system location, not the user's
  home directory itself, and (when configured) inside ``safety.allowed_roots``
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request

from patchquest.config import get_config
from patchquest.paths import FORBIDDEN_PREFIXES, _is_within

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "testserver"}
EXEMPT_PATHS = {"/api/health"}
SYSTEM_DIRS = tuple(Path(p) for p in (
    "/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev", "/proc", "/sys",
))


def is_loopback(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def configured_token() -> str | None:
    value = os.environ.get(get_config().api_token_env, "")
    return value or None


def check_startup_policy(host: str) -> None:
    """Refuse to listen on a non-loopback interface unless a token protects the API."""
    if not is_loopback(host) and not configured_token():
        raise RuntimeError(
            f"Refusing to listen on {host!r} without authentication. Set {get_config().api_token_env} "
            "to a long random value, or bind to 127.0.0.1."
        )


def allowed_hosts() -> list[str]:
    return sorted(LOOPBACK_HOSTS | set(get_config().allowed_hosts))


async def require_auth(request: Request) -> None:
    """FastAPI dependency enforcing the bearer token when one is configured."""
    if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
        return
    expected = configured_token()
    if expected is None:
        return
    supplied = ""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        supplied = header[7:].strip()
    elif request.url.path.endswith("/events/stream") or request.url.path.endswith("/stream"):
        supplied = request.query_params.get("token", "")
    if not supplied or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid API token",
                            headers={"WWW-Authenticate": "Bearer"})


class RepoPathError(ValueError):
    """The requested repository path is not acceptable for a run."""


def validate_repo_path(raw: str) -> str:
    """Return the canonical repository path or raise :class:`RepoPathError`."""
    if not raw or "\x00" in raw:
        raise RepoPathError("repo_path is required")
    path = Path(os.path.expanduser(raw))
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise RepoPathError(f"repo_path does not exist: {raw}") from None
    if not resolved.is_dir():
        raise RepoPathError(f"repo_path is not a directory: {raw}")
    if resolved == Path.home().resolve():
        raise RepoPathError("repo_path must be a project directory, not your home directory")
    for system in SYSTEM_DIRS:
        if resolved == system or (system != Path("/") and _is_within(resolved, system)):
            raise RepoPathError(f"repo_path is a system location: {resolved}")
    for forbidden in FORBIDDEN_PREFIXES:
        if _is_within(resolved, Path(forbidden)):
            raise RepoPathError("repo_path is inside a credentials directory")
    roots = get_config().safety.allowed_roots
    if roots and not any(_is_within(resolved, Path(os.path.expanduser(r)).resolve()) for r in roots):
        raise RepoPathError(f"repo_path is outside the allowed roots: {roots}")
    return str(resolved)

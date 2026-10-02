"""`patchquest doctor`: actionable environment and security diagnostics.

Each check is a small pure function returning a :class:`Check`, so they can be tested and
reused by the API/UI. A FAIL means PatchQuest cannot work or is unsafe; WARN means a feature
is unavailable; OK/INFO are informational. Every non-OK result says how to fix it.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def check_python() -> Check:
    v = sys.version_info
    if (v.major, v.minor) < (3, 11):
        return Check("python", FAIL, f"Python {v.major}.{v.minor} found; 3.11+ required", "Install Python 3.11 or newer")
    return Check("python", OK, f"Python {v.major}.{v.minor}.{v.micro}")


def check_dependencies() -> Check:
    missing = []
    for mod in ("fastapi", "pydantic", "httpx", "yaml", "sse_starlette", "uvicorn", "starlette"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return Check("dependencies", FAIL, f"missing: {', '.join(missing)}", "Run: pip install patchquest   (from a checkout: pip install -e backend)")
    return Check("dependencies", OK, "core dependencies import")


def check_tree_sitter() -> Check:
    try:
        importlib.import_module("tree_sitter")
        importlib.import_module("tree_sitter_python")
    except ImportError:
        return Check("tree-sitter", WARN, "not installed; symbol extraction falls back to regex",
                     'Run: pip install "patchquest[tree-sitter]"')
    return Check("tree-sitter", OK, "available")


def check_config() -> list[Check]:
    from patchquest.config import get_config
    from patchquest.security import configured_token, is_loopback

    out: list[Check] = []
    try:
        cfg = get_config()
    except Exception as exc:
        return [Check("config", FAIL, f"invalid configuration: {exc}", "Fix config.yaml or unset PATCHQUEST_CONFIG")]
    out.append(Check("config", OK, f"host={cfg.host} port={cfg.port} db={cfg.db_path}"))
    if not is_loopback(cfg.host) and not configured_token():
        out.append(Check("api-auth", FAIL, f"host {cfg.host} is not loopback and no API token is set",
                         f"Set {cfg.api_token_env} to a long random value or use host 127.0.0.1"))
    elif configured_token():
        out.append(Check("api-auth", OK, "API token required for all routes"))
    else:
        out.append(Check("api-auth", INFO, "loopback only, no token (single-user local mode)",
                         f"Set {cfg.api_token_env} to require a token"))
    if cfg.safety.allow_outside_repo:
        out.append(Check("safety", WARN, "allow_outside_repo is enabled", "Set safety.allow_outside_repo: false"))
    return out


def check_database() -> Check:
    from patchquest.database import get_db_path

    path = get_db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        return Check("database", FAIL, f"{path}: {exc}", "Check permissions or set db_path in config.yaml")
    if ok != "ok":
        return Check("database", FAIL, f"{path}: integrity_check={ok}", "Restore from backup or move the file aside")
    return Check("database", OK, f"{path} (journal={mode})")


def check_workspace_dir() -> Check:
    from patchquest.runtime.workspace import WORKSPACE_BASE

    try:
        WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=WORKSPACE_BASE):
            pass
        free = shutil.disk_usage(WORKSPACE_BASE).free / 2**30
    except OSError as exc:
        return Check("workspace", FAIL, f"{WORKSPACE_BASE}: {exc}", "Make the directory writable")
    if free < 1:
        return Check("workspace", WARN, f"{free:.1f} GiB free at {WORKSPACE_BASE}", "Free some disk space")
    return Check("workspace", OK, f"{WORKSPACE_BASE} writable, {free:.0f} GiB free")


def _tool(name: str, args: list[str], fix: str, level: str = WARN) -> Check:
    path = shutil.which(name)
    if not path:
        return Check(name, level, "not found on PATH", fix)
    try:
        out = subprocess.run([path, *args], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        return Check(name, OK, out[0] if out else path)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(name, level, str(exc), fix)


def check_docker() -> list[Check]:
    from patchquest.runtime.docker_runtime import check_docker_available, check_image_available

    if not shutil.which("docker"):
        return [Check("docker", WARN, "not installed; only the local runtime is available", "Install Docker to enable sandboxed runs")]
    if not check_docker_available():
        return [Check("docker", WARN, "installed but the daemon is not reachable", "Start Docker (e.g. `sudo systemctl start docker`)")]
    out = [Check("docker", OK, "daemon reachable")]
    if check_image_available():
        out.append(Check("sandbox-image", OK, "patchquest-sandbox:latest present"))
    else:
        out.append(Check("sandbox-image", WARN, "patchquest-sandbox:latest not built",
                         "Run: docker build -t patchquest-sandbox:latest docker/sandbox"))
    return out


def check_executor_isolation() -> Check:
    """Self-test: a credential in the parent environment must not reach model-chosen commands."""
    from patchquest.execution.executor import run_argv, scrubbed_env

    os.environ["PATCHQUEST_DOCTOR_API_KEY"] = "doctor-canary"
    try:
        res = run_argv([sys.executable, "-c", "import os;print(os.environ.get('PATCHQUEST_DOCTOR_API_KEY'))"],
                       tempfile.gettempdir(), timeout=10, env=scrubbed_env())
    finally:
        os.environ.pop("PATCHQUEST_DOCTOR_API_KEY", None)
    if res["stdout"].strip() != "None":
        return Check("sandbox-env", FAIL, "credentials leaked into a child process", "This is a bug; please report it")
    return Check("sandbox-env", OK, "child processes do not inherit credential-shaped variables")


def check_patch_engine() -> Check:
    """Self-test: a context diff applies exactly and a stale one is refused."""
    from patchquest.tools.patch_tools import apply_unified_diff

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "f.py").write_text("a = 1\nb = 2\nc = 3\n")
        good = apply_unified_diff("--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 9\n c = 3\n", tmp)
        stale = apply_unified_diff("--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 5\n c = 3\n", tmp)
        final = (Path(tmp) / "f.py").read_text()
    if good["success"] and not stale["success"] and final == "a = 1\nb = 9\nc = 3\n":
        return Check("patch-engine", OK, "verified application and stale-diff rejection work")
    return Check("patch-engine", FAIL, "self-test failed", "This is a bug; please report it")


def check_providers() -> list[Check]:
    from patchquest.providers.catalog import PROVIDER_CATALOG

    out = []
    for p in PROVIDER_CATALOG:
        env = p.get("api_key_env")
        if p["name"] == "mock":
            continue
        if env:
            ok = bool(os.environ.get(env))
            out.append(Check(f"provider:{p['name']}", INFO, f"{env} {'is set' if ok else 'is not set'}",
                             "" if ok else f"export {env}=..."))
        else:
            out.append(Check(f"provider:{p['name']}", INFO, f"endpoint {p.get('base_url') or 'configured per run'}"))
    return out


ALL_CHECKS: list[Callable[[], Check | list[Check]]] = [
    check_python, check_dependencies, check_tree_sitter, check_config, check_database, check_workspace_dir,
    lambda: _tool("git", ["--version"], "Install git", FAIL),
    lambda: _tool("node", ["--version"], "Install Node 18+ to build the dashboard"),
    check_docker, check_executor_isolation, check_patch_engine, check_providers,
]


def run_checks() -> list[Check]:
    results: list[Check] = []
    for fn in ALL_CHECKS:
        try:
            r = fn()
        except Exception as exc:
            results.append(Check(getattr(fn, "__name__", "check"), FAIL, f"check crashed: {exc}"))
            continue
        results.extend(r if isinstance(r, list) else [r])
    return results

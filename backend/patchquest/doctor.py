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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""
    impact: str = ""  # what stops working or becomes unsafe if this is wrong (set on every non-ok result)

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
    out.append(Check("config", OK, f"host={cfg.host} port={cfg.port}"))
    from patchquest.security import _database_has_tokens

    has_auth = bool(configured_token()) or _database_has_tokens()
    if not is_loopback(cfg.host) and not has_auth:
        out.append(Check("api-auth", FAIL, f"host {cfg.host} is not loopback and no API token exists",
                         f"Create one with `patchquest admin init`, or set {cfg.api_token_env}, or use host 127.0.0.1",
                         "anyone who can reach the port can run commands as the user"))
    elif has_auth:
        out.append(Check("api-auth", OK, "API tokens required for all /api routes"))
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


def check_schema() -> Check:
    from patchquest.database import get_db, get_db_path
    from patchquest.persistence.migrations import current_version
    from patchquest.persistence.schema import MIGRATIONS

    if not get_db_path().exists():
        return Check("schema", INFO, "no database yet; it is created on first use")
    try:
        with get_db() as conn:
            applied = current_version(conn)
    except sqlite3.Error as exc:
        return Check("schema", FAIL, str(exc), "Check the database file", "no run history is available")
    latest = max(m.version for m in MIGRATIONS)
    if applied > latest:
        return Check("schema", FAIL, f"database is v{applied}, this release understands v{latest}", "Upgrade PatchQuest",
                     "the API refuses to start so a newer database is never damaged")
    if applied < latest:
        return Check("schema", INFO, f"database v{applied} will be migrated to v{latest} on start (a backup is written first)")
    return Check("schema", OK, f"v{applied}, up to date")


def check_state_permissions() -> Check:
    home_state = Path.home() / ".patchquest"
    if not home_state.exists():
        return Check("state-permissions", OK, "no state directory yet")
    mode = home_state.stat().st_mode & 0o777
    if mode & 0o077:
        return Check("state-permissions", WARN, f"{home_state} is {oct(mode)} (readable by other users)", f"chmod 700 {home_state}",
                     "run history holds task text, command output and file contents")
    return Check("state-permissions", OK, f"{home_state} is owner-only")


def check_git_hardening() -> Check:
    """A hostile .git/config must not run code when PatchQuest reads a repository's state."""
    git = shutil.which("git")
    if git is None:
        return Check("git-hardening", INFO, "git is not installed")
    from patchquest.runtime import fingerprint

    with tempfile.TemporaryDirectory() as tmp:
        repo, marker = Path(tmp) / "r", Path(tmp) / "ran"
        repo.mkdir()
        hook = Path(tmp) / "hook.sh"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        try:
            # A commit must exist before `status` does real work, and the hostile setting is added *after* it, so that
            # setting it up cannot itself trigger the hook: only the read under test can.
            for args in (["init", "-q"], ["-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "--allow-empty", "-m", "x"],
                         ["config", "core.fsmonitor", str(hook)]):
                subprocess.run([git, "-C", str(repo), *args], check=True, capture_output=True, env=env, timeout=10)
            fingerprint.compute(str(repo))
        except (OSError, subprocess.SubprocessError) as exc:
            return Check("git-hardening", INFO, f"could not exercise git: {type(exc).__name__}")
        if marker.exists():
            return Check("git-hardening", FAIL, "a repository's own git config executed code during a read",
                         "Report this: the hardened git wrapper is not neutralising core.fsmonitor",
                         "opening a hostile repository could run its code")
    return Check("git-hardening", OK, "a hostile .git/config does not run when repository state is read")


def check_queue_and_runs() -> list[Check]:
    from datetime import UTC, datetime, timedelta

    from patchquest.database import get_db, get_db_path

    if not get_db_path().exists():
        return []
    out: list[Check] = []
    try:
        from patchquest.runtime import queue

        stats = queue.stats()
        with get_db() as conn:
            cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            stale = conn.execute(
                "SELECT COUNT(*) FROM runs r WHERE r.status IN ('running', 'waiting_approval') AND r.lease_owner IS NULL AND "
                "r.updated_at < ? AND NOT EXISTS (SELECT 1 FROM run_events e WHERE e.run_id = r.id AND e.created_at >= ?)",
                (cutoff, cutoff)).fetchone()[0]
    except sqlite3.Error:
        return []
    if stats["expired_leases"]:
        out.append(Check("queue", WARN, f"{stats['expired_leases']} run(s) lost their worker (lease expired)",
                         "Start a worker (`patchquest worker`): it recovers them; check `patchquest queue`",
                         "those runs make no progress until a worker polls"))
    elif stats["queued"]:
        wait = stats["oldest_wait_s"] or 0
        out.append(Check("queue", WARN if wait > 300 else INFO, f"{stats['queued']} run(s) queued, oldest waiting {wait:.0f}s",
                         "Start more workers (`patchquest worker`)" if wait > 300 else "", "queued runs wait for a worker" if wait > 300 else ""))
    else:
        out.append(Check("queue", OK, "no runs waiting; no expired leases"))
    if stale:
        out.append(Check("stale-runs", WARN, f"{stale} run(s) marked running with no activity for over an hour and no worker lease",
                         "Restart PatchQuest (startup recovery marks them interrupted) then `patchquest resume RUN --plan`",
                         "they look active but nothing is executing them"))
    return out


def check_engines() -> Check:
    import asyncio

    from patchquest.providers.engines import engine_report

    try:
        # In its own thread so this also works when doctor is called from code that already has an event loop running.
        with ThreadPoolExecutor(max_workers=1) as pool:
            rows = pool.submit(lambda: asyncio.run(engine_report())).result(timeout=30)
    except Exception:
        return Check("local-engines", INFO, "could not probe local engines")
    up = [r["engine"] for r in rows if r["available"]]
    if not up:
        return Check("local-engines", INFO, "no local model engine is running (sglang, vllm, llamacpp, lmstudio, ollama)",
                     "Start one, e.g. `python -m sglang.launch_server --model-path Qwen/Qwen3-0.6B`", "runs need a cloud provider or the mock model")
    return Check("local-engines", OK, ", ".join(f"{r['engine']} ({r['latency_ms']}ms)" for r in rows if r["available"]))


ALL_CHECKS: list[Callable[[], Check | list[Check]]] = [
    check_python, check_dependencies, check_tree_sitter, check_config, check_database, check_workspace_dir,
    lambda: _tool("git", ["--version"], "Install git", FAIL),
    lambda: _tool("node", ["--version"], "Install Node 18+ to build the dashboard"),
    check_schema, check_state_permissions, check_git_hardening, check_queue_and_runs, check_engines,
    check_docker, check_executor_isolation, check_patch_engine, check_providers,
]


IMPACT = {
    "python": "PatchQuest will not start", "dependencies": "PatchQuest will not start", "tree-sitter": "symbol-level context is less precise",
    "config": "PatchQuest falls back to defaults or refuses to start", "database": "no run history can be stored",
    "workspace": "no run can create its isolated workspace", "git": "diffs and repository checks fail", "node": "the dashboard cannot be built",
    "docker": "the Docker sandbox runtime is unavailable", "sandbox-image": "--runtime docker cannot start", "sandbox-env": "model-chosen commands could see credentials",
    "patch-engine": "edits may be applied incorrectly", "api-auth": "the API is open to anyone who can reach it",
}


def run_checks() -> list[Check]:
    results: list[Check] = []
    for fn in ALL_CHECKS:
        try:
            r = fn()
        except Exception as exc:
            results.append(Check(getattr(fn, "__name__", "check"), FAIL, f"check crashed: {exc}"))
            continue
        results.extend(r if isinstance(r, list) else [r])
    for c in results:
        if c.status in (WARN, FAIL) and not c.impact:
            c.impact = IMPACT.get(c.name) or ("that provider cannot be used" if c.name.startswith("provider:") else "")
    return results

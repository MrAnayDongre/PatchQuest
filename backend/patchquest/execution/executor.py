"""Subprocess execution with the guarantees the rest of PatchQuest relies on.

* ``shell=False`` unless a human explicitly approved a composite command.
* The child runs in its own process group; a timeout kills the whole group (no orphans).
* Output is captured with a hard memory cap while still being drained, so a runaway
  command can neither block on a full pipe nor exhaust memory.
* The environment is an allow-list. Credentials in the parent environment never reach
  model-chosen commands.
"""

from __future__ import annotations

import fnmatch
import os
import signal
import subprocess
import threading
import time
from typing import Any

from patchquest.tools.secret_guard import redact_secrets

ENV_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_*", "TERM", "TMPDIR", "TZ", "SHELL",
    "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE", "NODE_*", "NPM_CONFIG_CACHE",
    "CARGO_HOME", "RUSTUP_HOME", "GOPATH", "GOCACHE", "GOFLAGS", "JAVA_HOME", "CI",
)
ENV_DENY_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "PRIVATE", "AUTH", "COOKIE")


def scrubbed_env(extra: dict[str, str] | None = None, passthrough: tuple[str, ...] = ()) -> dict[str, str]:
    """Build a child environment from the allow-list, dropping anything credential-shaped."""
    allow = ENV_ALLOW + tuple(passthrough)
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if any(bad in upper for bad in ENV_DENY_SUBSTRINGS):
            continue
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in allow):
            env[name] = value
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    # Validation re-runs the same files right after edits. Python's pyc check compares whole-second
    # mtime and size, so a same-size edit within one second would silently run stale bytecode.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


class _Capture(threading.Thread):
    """Drain a pipe, keeping at most ``limit`` bytes."""

    def __init__(self, stream: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self.stream, self.limit = stream, limit
        self.chunks: list[bytes] = []
        self.total = 0

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read(65536)
                if not chunk:
                    return
                if self.total < self.limit:
                    self.chunks.append(chunk[: self.limit - self.total])
                self.total += len(chunk)
        except (OSError, ValueError):
            return

    @property
    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self.total > self.limit


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _run(args: list[str], cwd: str, timeout: float, max_output: int, env: dict[str, str] | None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            args, cwd=cwd, env=env if env is not None else scrubbed_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return _result(False, -1, "", f"Command execution error: {exc}", False, False, started)

    out, err = _Capture(proc.stdout, max_output), _Capture(proc.stderr, max_output)
    out.start()
    err.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        proc.wait()
    # Surviving grandchildren in the group would keep the pipes open; reap them.
    if not timed_out:
        _kill_group_quietly(proc)
    out.join(timeout=5)
    err.join(timeout=5)

    stderr = err.text
    if timed_out:
        stderr = (stderr + f"\nCommand timed out after {timeout:g}s and was killed").strip()
    return _result(
        proc.returncode == 0 and not timed_out,
        -1 if timed_out else proc.returncode,
        out.text, stderr, out.truncated or err.truncated, timed_out, started,
    )


def _kill_group_quietly(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _result(success: bool, code: int, stdout: str, stderr: str, truncated: bool, timed_out: bool, started: float) -> dict[str, Any]:
    return {
        "success": success,
        "returncode": code,
        "stdout": redact_secrets(stdout),
        "stderr": redact_secrets(stderr),
        "truncated": truncated,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 3),
    }


def run_argv(argv: list[str], cwd: str, timeout: float = 60, max_output: int = 1_000_000,
             env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run ``argv`` directly (no shell)."""
    return _run(list(argv), cwd, timeout, max_output, env)


def run_shell(command: str, cwd: str, timeout: float = 60, max_output: int = 1_000_000,
              env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run ``command`` through ``/bin/sh``. Only for commands a human approved."""
    return _run(["/bin/sh", "-c", command], cwd, timeout, max_output, env)

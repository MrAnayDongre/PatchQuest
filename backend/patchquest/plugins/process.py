"""Run one call of an external-process plugin.

The plugin is started fresh per call in its own session with: a near-empty environment, its own directory as
working directory, a timeout (the whole process group is killed), and kernel limits on CPU time, memory,
open files and the size of what it can write. It speaks one JSON object on stdin and answers with one JSON
line on stdout. This contains runaway or crashing plugins; it does not stop one from reading files or opening
sockets the user account can reach.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

CPU_SECONDS = 20
MEMORY_MB = 1024
OPEN_FILES = 64
MAX_OUTPUT_KB = 2048


class ProcessFailure(RuntimeError):
    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def resolve_command(command: tuple[str, ...], plugin_dir: Path) -> list[str]:
    """A path-like first element must stay inside the plugin's directory; a bare name comes from PATH."""
    head = command[0]
    if "/" in head or head.startswith("."):
        target = (plugin_dir / head).resolve()
        if not target.is_relative_to(plugin_dir.resolve()):
            raise ProcessFailure(f"command {head!r} points outside the plugin directory")
        return [str(target), *command[1:]]
    return list(command)


def call(command: tuple[str, ...], plugin_dir: Path, request: dict[str, Any], timeout: float) -> dict[str, Any]:
    argv = resolve_command(command, plugin_dir)
    limits = f"ulimit -t {CPU_SECONDS}; ulimit -v {MEMORY_MB * 1024}; ulimit -n {OPEN_FILES}; ulimit -f {MAX_OUTPUT_KB}; "
    shell = limits + "exec " + " ".join(shlex.quote(a) for a in argv)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(  # noqa: S603 - the command is from an operator-installed manifest, quoted and run under ulimits
            ["/bin/sh", "-c", shell], stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=plugin_dir, env=env,
                                start_new_session=True)
        try:
            proc.communicate(json.dumps(request).encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise ProcessFailure(f"timed out after {timeout:g}s", timed_out=True) from None
        finally:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)  # nothing the plugin left running outlives the call
        out.seek(0)
        err.seek(0)
        raw, stderr = out.read(MAX_OUTPUT_KB * 1024 + 1), err.read(2000).decode(errors="replace").strip()
    if proc.returncode != 0:
        raise ProcessFailure(f"exited with status {proc.returncode}" + (f": {stderr[-300:]}" if stderr else ""))
    if len(raw) > MAX_OUTPUT_KB * 1024:
        raise ProcessFailure("produced more output than allowed")
    try:
        reply = json.loads(raw.decode().strip().splitlines()[-1])
    except (ValueError, IndexError, UnicodeDecodeError):
        raise ProcessFailure("did not answer with a JSON line") from None
    if not isinstance(reply, dict):
        raise ProcessFailure("answered with something other than a JSON object")
    return reply


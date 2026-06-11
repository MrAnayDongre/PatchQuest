"""Safe command execution with timeouts, output caps, and secret redaction."""

from __future__ import annotations

import subprocess
from typing import Any

from patchquest.config import get_config
from patchquest.tools.secret_guard import redact_secrets


def run_command_safe(
    command: str,
    cwd: str,
    timeout: int | None = None,
    max_output: int | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    config = get_config()
    timeout = timeout or config.safety.max_command_timeout
    max_output = max_output or config.safety.max_output_bytes

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        stdout = result.stdout[:max_output] if result.stdout else ""
        stderr = result.stderr[:max_output] if result.stderr else ""

        stdout = redact_secrets(stdout)
        stderr = redact_secrets(stderr)

        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": len(result.stdout or "") > max_output or len(result.stderr or "") > max_output,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "truncated": False,
        }
    except Exception as e:
        return {
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command execution error: {e}",
            "truncated": False,
        }

"""Policy-enforcing command execution.

Every command is classified first. Blocked commands never run; commands that need approval
only run when the caller passes ``approved=True`` (the orchestrator does this after a human
approves). Automatic commands run without a shell, in a scrubbed environment.
"""

from __future__ import annotations

from typing import Any

from patchquest.config import get_config
from patchquest.execution.executor import run_argv, run_shell, scrubbed_env
from patchquest.tools.command_risk import RiskLevel, classify


def _refusal(code: int, message: str, **extra: Any) -> dict[str, Any]:
    return {"success": False, "returncode": code, "stdout": "", "stderr": message, "truncated": False,
            "timed_out": False, **extra}


def run_command_safe(
    command: str,
    cwd: str,
    timeout: int | None = None,
    max_output: int | None = None,
    env: dict[str, str] | None = None,
    approved: bool = False,
) -> dict[str, Any]:
    config = get_config()
    timeout = timeout or config.safety.max_command_timeout
    max_output = max_output or config.safety.max_output_bytes

    decision = classify(command, cwd)
    if decision.level == RiskLevel.BLOCKED:
        return _refusal(-2, f"Blocked: {decision.reason}", blocked=True, risk=decision.level.value)
    if decision.level == RiskLevel.RISKY_ASK and not approved:
        return _refusal(-3, f"Approval required: {decision.reason}", needs_approval=True,
                        risk=decision.level.value, reason=decision.reason)

    child_env = env if env is not None else scrubbed_env(passthrough=tuple(config.safety.env_passthrough))
    if decision.shell_syntax or not decision.argv:
        result = run_shell(command, cwd, timeout, max_output, child_env)
    else:
        result = run_argv(decision.argv, cwd, timeout, max_output, child_env)
    result["risk"] = decision.level.value
    return result

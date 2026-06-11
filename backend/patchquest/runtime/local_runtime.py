"""Local runtime - executes commands on the host system with safety."""

from __future__ import annotations

from typing import Any

from patchquest.runtime.sandbox_base import RuntimeBase
from patchquest.tools.command_runner import run_command_safe


class LocalRuntime(RuntimeBase):
    def run_command(self, command: str, cwd: str, timeout: int = 60) -> dict[str, Any]:
        return run_command_safe(command, cwd, timeout=timeout)

    def is_available(self) -> bool:
        return True

"""Base sandbox/runtime interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RuntimeBase(ABC):
    @abstractmethod
    def run_command(self, command: str, cwd: str, timeout: int = 60) -> dict[str, Any]:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

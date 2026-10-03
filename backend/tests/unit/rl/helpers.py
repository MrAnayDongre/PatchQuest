"""Shared fixtures for the gym tests: a tiny task with exactly two visible tests and a hidden oracle."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from patchquest.evaluation.tasks import EvalTask

TEST_CMD = "python3 -m unittest discover -s tests -q"
FIX = {"type": "edit", "path": "m.py", "search": "return 1", "replace": "return 2"}
ORACLE_MARKER = "hidden_oracle_marker_value_314159"

_VISIBLE_TEST = """\
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from m import f


class T(unittest.TestCase):
    def test_positive(self):
        self.assertGreater(f(), 0)

    def test_two(self):
        self.assertEqual(f(), 2)
"""
_ORACLE_TEST = f"""\
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from m import f


class O(unittest.TestCase):
    def test_oracle(self):
        {ORACLE_MARKER} = 2
        self.assertEqual(f(), {ORACLE_MARKER})
"""


def simple_task(**overrides: Any) -> EvalTask:
    base: dict[str, Any] = {
        "id": "unit-simple", "category": "bug_fix", "difficulty": "easy", "task": "Make f() return 2.",
        "files": {"m.py": "def f():\n    return 1\n", "tests/test_m.py": _VISIBLE_TEST},
        "test_command": TEST_CMD, "oracle_command": TEST_CMD,
        "oracle_files": {"tests/test_oracle.py": _ORACLE_TEST},
        "solution": {"edits": [{"path": "m.py", "search": "return 1", "replace": "return 2"}]},
    }
    base.update(overrides)
    return EvalTask(**base)


class ListPolicy:
    """Plays a fixed list of actions, then finishes."""

    def __init__(self, actions: list[Any]) -> None:
        self._actions = actions
        self._queue: list[Any] = []

    def reset(self, observation: dict[str, Any]) -> None:
        self._queue = list(self._actions)

    def act(self, observation: dict[str, Any]) -> Any:
        return self._queue.pop(0) if self._queue else {"type": "finish"}


def snapshot(root: Path) -> dict[str, str]:
    """rel path -> sha256 of every regular file under ``root``."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}

"""Throwaway repositories and the canonical "calc" bug used across pipeline tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any


def _make_test_repo() -> str:
    """A tiny private repository. Tests must never index real host directories like /tmp."""
    root = Path(tempfile.mkdtemp(prefix="pq-test-repo-"))
    (root / "README.md").write_text("# Test Repo\n\nA tiny repository for pipeline tests.\n")
    (root / "app.py").write_text("def main():\n    return 0\n")
    return str(root)


TEST_REPO = _make_test_repo()

TEST_CMD = "python3 -m unittest discover -s tests -q"
CALC_BUG = "def add(a, b):\n    return a - b\n"
CALC_TEST = (
    "import os, sys, unittest\n"
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
    "from calc import add\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n"
)
TASK = "Fix add() in calc.py so it returns the sum"
PLAN: dict[str, Any] = {
    "plan": "fix add", "files_to_inspect": ["calc.py"], "tests_likely_needed": [],
    "expected_patch_scope": "1 file", "stop_conditions": [], "test_commands": [TEST_CMD],
}


def edit(search: str, replace: str, path: str = "calc.py") -> dict[str, Any]:
    return {"edits": [{"path": path, "search": search, "replace": replace}], "create": [], "delete": [], "rationale": ""}


FIX = edit("return a - b", "return a + b") | {"rationale": "add should add"}
WRONG = edit("return a - b", "return a * b") | {"rationale": "wrong on purpose"}


def make_calc_repo(root: Path) -> Path:
    """calc.py with the bug plus a unittest that fails until it is fixed."""
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(CALC_BUG)
    (root / "tests" / "test_calc.py").write_text(CALC_TEST)
    (root / "README.md").write_text("# calc\n")
    return root

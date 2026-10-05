"""Detect and run test/check commands for a repository."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def _has_module(name: str) -> bool:
    """Whether the interpreter that will actually run the tests (the one on PATH) can import ``name``."""
    import subprocess

    interpreter = shutil.which(_python())
    if not interpreter:
        return False
    try:
        return subprocess.run([interpreter, "-c", f"import {name}"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _python() -> str:
    """`python` is absent on many modern systems that only ship `python3`."""
    return "python" if shutil.which("python") else "python3"


def detect_test_commands(repo_path: str) -> list[str]:
    commands: list[str] = []
    root = Path(repo_path)

    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        commands.append(f"{_python()} -m pytest --tb=short -q")

    if (root / "tox.ini").exists():
        commands.append("tox")

    # Plain layouts without pyproject/pytest.ini: tests/ or test_*.py at the top level.
    has_py_tests = (root / "tests").is_dir() and any((root / "tests").rglob("test_*.py"))
    has_py_tests = has_py_tests or any(root.glob("test_*.py"))
    if has_py_tests and not commands:
        if _has_module("pytest"):
            commands.append(f"{_python()} -m pytest --tb=short -q")
        else:
            start = "tests" if (root / "tests").is_dir() else "."
            commands.append(f"{_python()} -m unittest discover -s {start} -q")

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})
            if "test" in scripts:
                commands.append("npm test")
        except (json.JSONDecodeError, OSError):
            pass

    if (root / "Cargo.toml").exists():
        commands.append("cargo test")

    if (root / "Makefile").exists():
        makefile = (root / "Makefile").read_text(errors="replace")
        if "test:" in makefile:
            commands.append("make test")

    if (root / "go.mod").exists():
        commands.append("go test ./...")

    return commands


def detect_check_commands(repo_path: str) -> list[str]:
    commands: list[str] = []
    root = Path(repo_path)

    if (root / "pyproject.toml").exists():
        pyproject = (root / "pyproject.toml").read_text(errors="replace")
        if "ruff" in pyproject:
            commands.append("ruff check .")
        if "mypy" in pyproject:
            commands.append("mypy .")

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})
            if "typecheck" in scripts:
                commands.append("npm run typecheck")
            if "lint" in scripts:
                commands.append("npm run lint")
            if "build" in scripts:
                commands.append("npm run build")
        except (json.JSONDecodeError, OSError):
            pass

    if (root / "Cargo.toml").exists():
        commands.append("cargo check")
        commands.append("cargo clippy")

    return commands

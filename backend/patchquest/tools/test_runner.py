"""Detect and run test/check commands for a repository."""

from __future__ import annotations

import json
import os
from pathlib import Path


def detect_test_commands(repo_path: str) -> list[str]:
    commands: list[str] = []
    root = Path(repo_path)

    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        commands.append("python -m pytest --tb=short -q")

    if (root / "tox.ini").exists():
        commands.append("tox")

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

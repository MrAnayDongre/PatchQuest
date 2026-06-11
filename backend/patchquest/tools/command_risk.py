"""Deterministic command risk classifier.

Classifies shell commands into risk categories without using LLM inference.
"""

from __future__ import annotations

import os
import re
import shlex
from enum import Enum


class RiskLevel(str, Enum):
    NO_RISK_AUTO = "no_risk_auto"
    CAREFUL_AUTO = "careful_auto"
    RISKY_ASK = "risky_ask"
    BLOCKED = "blocked"


NO_RISK_COMMANDS = frozenset({
    "pwd", "ls", "echo", "cat", "head", "tail", "wc", "sort", "uniq",
    "grep", "rg", "find", "which", "whoami", "date", "true", "false",
    "git status", "git diff", "git log", "git branch", "git show",
    "git rev-parse", "git remote -v", "git tag",
})

NO_RISK_EXECUTABLES = frozenset({
    "pwd", "ls", "echo", "cat", "head", "tail", "wc", "sort", "uniq",
    "grep", "rg", "find", "which", "whoami", "date", "true", "false",
    "file", "stat", "du", "df",
})

CAREFUL_EXECUTABLES = frozenset({
    "pytest", "python", "node", "npm", "npx", "pnpm", "yarn",
    "cargo", "go", "make", "ruff", "mypy", "black", "isort",
    "eslint", "prettier", "tsc", "javac", "gcc", "g++", "clang",
    "rustc", "dotnet",
})

CAREFUL_COMMANDS = frozenset({
    "python -m pytest", "npm test", "npm run test", "npm run build",
    "npm run typecheck", "npm run lint", "cargo test", "cargo check",
    "cargo clippy", "cargo fmt", "go test", "make test", "make check",
    "ruff check", "ruff format --check", "mypy", "black --check",
    "python -m pytest", "go test ./...",
})

RISKY_EXECUTABLES = frozenset({
    "pip", "pip3", "npm", "pnpm", "yarn", "cargo",
    "chmod", "chown", "mv", "cp", "rm",
    "curl", "wget", "docker", "podman",
    "git",
})

RISKY_GIT_SUBCOMMANDS = frozenset({
    "checkout", "reset", "clean", "stash", "rebase", "merge",
    "cherry-pick", "revert", "pull",
})

BLOCKED_PATTERNS = [
    re.compile(r"rm\s+(-[a-z]*f[a-z]*\s+)?(/|~|\$HOME)\s*$"),
    re.compile(r"rm\s+-rf\s+/"),
    re.compile(r"rm\s+-rf\s+~"),
    re.compile(r"sudo\s+rm\s+-rf"),
    re.compile(r"mkfs"),
    re.compile(r"dd\s+.*of=/dev/"),
    re.compile(r"shutdown"),
    re.compile(r"reboot"),
    re.compile(r":\(\)\s*\{"),  # fork bomb
    re.compile(r"curl\s+.*\|\s*(ba)?sh"),
    re.compile(r"wget\s+.*\|\s*(ba)?sh"),
    re.compile(r"curl\s+.*\|\s*sudo"),
    re.compile(r"wget\s+.*\|\s*sudo"),
    re.compile(r"docker\s+system\s+prune\s+-a"),
    re.compile(r"git\s+push\s+.*--force"),
    re.compile(r"git\s+push\s+-f"),
]

BLOCKED_PATH_READS = [
    os.path.expanduser("~/.ssh"),
    os.path.expanduser("~/.aws"),
    os.path.expanduser("~/.gcp"),
    os.path.expanduser("~/.azure"),
    os.path.expanduser("~/.config/gcloud"),
]


def classify_command(command: str, repo_path: str | None = None) -> tuple[RiskLevel, str]:
    """Classify a command's risk level. Returns (level, reason)."""
    stripped = command.strip()

    for pattern in BLOCKED_PATTERNS:
        if pattern.search(stripped):
            return RiskLevel.BLOCKED, "Command matches a blocked destructive pattern."

    for blocked_path in BLOCKED_PATH_READS:
        if blocked_path in stripped:
            return RiskLevel.BLOCKED, f"Command accesses sensitive path: {blocked_path}"

    if "sudo" in stripped.split():
        return RiskLevel.BLOCKED, "Sudo commands are blocked."

    if stripped in NO_RISK_COMMANDS:
        return RiskLevel.NO_RISK_AUTO, "Read-only command."

    try:
        parts = shlex.split(stripped)
    except ValueError:
        return RiskLevel.RISKY_ASK, "Could not parse command safely."

    if not parts:
        return RiskLevel.NO_RISK_AUTO, "Empty command."

    executable = parts[0]

    if executable in ("python", "python3", "node"):
        if len(parts) == 2 and parts[1] == "--version":
            return RiskLevel.NO_RISK_AUTO, "Version check."

    if executable in NO_RISK_EXECUTABLES:
        if executable == "cat" and len(parts) > 1:
            for p in parts[1:]:
                if not p.startswith("-"):
                    for blocked_path in BLOCKED_PATH_READS:
                        if os.path.expanduser(p).startswith(blocked_path):
                            return RiskLevel.BLOCKED, f"Reading sensitive path: {p}"
        return RiskLevel.NO_RISK_AUTO, "Safe read-only command."

    if executable == "git":
        git_sub = parts[1] if len(parts) > 1 else ""
        if git_sub in ("status", "diff", "log", "branch", "show", "rev-parse", "remote", "tag"):
            return RiskLevel.NO_RISK_AUTO, "Read-only git command."
        if git_sub == "push" and "--force" in parts:
            return RiskLevel.BLOCKED, "Force push is blocked."
        if git_sub == "push":
            return RiskLevel.RISKY_ASK, "Git push requires approval."
        if git_sub in RISKY_GIT_SUBCOMMANDS:
            return RiskLevel.RISKY_ASK, f"Git {git_sub} modifies repository state."
        return RiskLevel.CAREFUL_AUTO, f"Git command: {git_sub}"

    for cmd_prefix in CAREFUL_COMMANDS:
        if stripped.startswith(cmd_prefix):
            return RiskLevel.CAREFUL_AUTO, "Test/check command."

    if executable in CAREFUL_EXECUTABLES:
        if executable in ("npm", "pnpm", "yarn"):
            if len(parts) > 1 and parts[1] == "install":
                return RiskLevel.RISKY_ASK, "Package installation may modify lockfiles and download packages."
            if len(parts) > 1 and parts[1] in ("test", "run"):
                return RiskLevel.CAREFUL_AUTO, "Running project script."
        if executable in ("pip", "pip3"):
            if len(parts) > 1 and parts[1] == "install":
                return RiskLevel.RISKY_ASK, "Package installation downloads and installs packages."
            return RiskLevel.CAREFUL_AUTO, "Pip command."
        if executable == "cargo" and len(parts) > 1 and parts[1] == "update":
            return RiskLevel.RISKY_ASK, "Cargo update modifies lockfile."
        return RiskLevel.CAREFUL_AUTO, "Build/test tool."

    if executable in RISKY_EXECUTABLES:
        return RiskLevel.RISKY_ASK, "Command may modify files or access network."

    if "|" in stripped:
        return RiskLevel.RISKY_ASK, "Piped command requires review."

    return RiskLevel.RISKY_ASK, "Unknown command requires approval."

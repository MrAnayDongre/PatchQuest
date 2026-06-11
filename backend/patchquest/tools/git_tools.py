"""Git CLI tools with safety."""

from __future__ import annotations

from patchquest.tools.command_runner import run_command_safe


def git_status(repo_path: str) -> dict:
    return run_command_safe("git status --porcelain", repo_path)


def git_diff(repo_path: str, staged: bool = False) -> dict:
    cmd = "git diff --staged" if staged else "git diff"
    return run_command_safe(cmd, repo_path)


def git_log(repo_path: str, count: int = 10) -> dict:
    return run_command_safe(f"git log --oneline -n {count}", repo_path)


def git_branch(repo_path: str) -> dict:
    return run_command_safe("git branch --show-current", repo_path)

